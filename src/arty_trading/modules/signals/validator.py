"""
Validateur de signaux — vérifie que toutes les conditions SMC/ICT sont réunies
avant d'autoriser un trade.

Le ``SignalValidator`` ne génère jamais de signal : il valide ou invalide un
signal déjà produit par le ``SignalGenerator``. Chaque condition est vérifiée
indépendamment et journalisée avec un statut VALIDÉE / ÉCHOUÉE.

Conditions vérifiées (toutes doivent être validées pour autoriser le trade) :
1. **Tendance HTF** (Higher Timeframe) alignée avec la direction du signal
2. **BOS** (Break of Structure) valide dans la direction du signal
3. **CHoCH** (Change of Character) valide — pas de CHoCH contradictoire
4. **Order Block** valide dans la direction du signal
5. **FVG** (Fair Value Gap) valide dans la direction du signal
6. **Liquidity Sweep** confirmé dans la direction du signal
7. **Premium / Discount** correct (BUY en discount, SELL en premium)
8. **Session** de trading autorisée
9. **Spread** acceptable
10. **News filter** (pas de news à impact élevé)
11. **Ratio Risque/Rendement** (RR) minimum respecté

Le validateur retourne un objet ``ValidationResult`` contenant :
- ``is_valid`` : True si toutes les conditions sont validées
- ``score`` : proportion de conditions validées (0.0 à 1.0)
- ``failed_conditions`` : liste des conditions échouées
- ``explanation`` : résumé textuel du résultat
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, LogCategory, SMCConcept, TradingSession
from arty_trading.logging.logger import get_logger
from arty_trading.modules.smc.sessions import SessionDetector

logger = get_logger(LogCategory.SIGNAL)

# =============================================================================
# Noms des conditions (pour la journalisation et failed_conditions)
# =============================================================================

COND_HTF_TREND = "htf_trend"
COND_BOS = "bos_valid"
COND_CHOCH = "choch_valid"
COND_ORDER_BLOCK = "order_block_valid"
COND_FVG = "fvg_valid"
COND_LIQUIDITY_SWEEP = "liquidity_sweep_confirmed"
COND_PREMIUM_DISCOUNT = "premium_discount_correct"
COND_SESSION = "session_authorized"
COND_SPREAD = "spread_acceptable"
COND_NEWS = "news_filter"
COND_RR = "min_rr"

ALL_CONDITIONS: tuple[str, ...] = (
    COND_HTF_TREND,
    COND_BOS,
    COND_CHOCH,
    COND_ORDER_BLOCK,
    COND_FVG,
    COND_LIQUIDITY_SWEEP,
    COND_PREMIUM_DISCOUNT,
    COND_SESSION,
    COND_SPREAD,
    COND_NEWS,
    COND_RR,
)


# =============================================================================
# Résultat de validation
# =============================================================================


@dataclass
class ValidationResult:
    """
    Résultat de la validation d'un signal.

    Attributes:
        is_valid: True si toutes les conditions sont validées
        score: Proportion de conditions validées (0.0 à 1.0)
        failed_conditions: Liste des noms des conditions échouées
        explanation: Résumé textuel du résultat
        checked_conditions: Détail condition par condition (nom → bool)
        details: Détail textuel par condition (nom → description)
    """

    is_valid: bool
    score: float
    failed_conditions: list[str] = field(default_factory=list)
    explanation: str = ""
    checked_conditions: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convertit le résultat en dictionnaire."""
        return {
            "is_valid": self.is_valid,
            "score": round(self.score, 4),
            "failed_conditions": list(self.failed_conditions),
            "explanation": self.explanation,
            "checked_conditions": dict(self.checked_conditions),
            "details": dict(self.details),
        }


# =============================================================================
# Validateur de signaux
# =============================================================================


class SignalValidator:
    """
    Validateur de signaux de trading.

    Vérifie que toutes les conditions SMC/ICT sont réunies avant d'autoriser
    un trade. Chaque condition est vérifiée indépendamment et journalisée.

    Le trade est autorisé uniquement si **toutes** les conditions sont
    validées. Le score représente la proportion de conditions validées.

    Usage typique ::

        validator = SignalValidator(min_risk_reward=1.5, max_spread=20)
        result = validator.validate(signal, candles, smc_data)
        if result.is_valid:
            # Autoriser le trade
            ...
    """

    def __init__(
        self,
        min_risk_reward: float = 1.5,
        max_spread: int = 20,
        authorized_sessions: list[TradingSession] | None = None,
        require_htf_alignment: bool = True,
        require_news_filter: bool = True,
        session_detector: SessionDetector | None = None,
    ) -> None:
        """
        Initialise le validateur de signaux.

        Args:
            min_risk_reward: Ratio risque/rendement minimum (défaut 1.5)
            max_spread: Spread maximum autorisé en points (défaut 20)
            authorized_sessions: Sessions autorisées (défaut: London + New York
                + overlap). Si None, utilise les sessions par défaut.
            require_htf_alignment: Vérifier l'alignement de la tendance HTF
            require_news_filter: Vérifier le filtre de news
            session_detector: Détecteur de sessions (créé par défaut si None)
        """
        self._min_rr = min_risk_reward
        self._max_spread = max_spread
        self._authorized_sessions = authorized_sessions or [
            TradingSession.LONDON,
            TradingSession.NEW_YORK,
            TradingSession.OVERLAP_LONDON_NY,
        ]
        self._require_htf_alignment = require_htf_alignment
        self._require_news_filter = require_news_filter
        self._session_detector = session_detector or SessionDetector()

        logger.info(
            "SignalValidator initialisé | min_rr=%.2f | max_spread=%d | "
            "sessions=%s | htf_alignment=%s | news_filter=%s",
            self._min_rr,
            self._max_spread,
            [s.value for s in self._authorized_sessions],
            self._require_htf_alignment,
            self._require_news_filter,
        )

    # -------------------------------------------------------------------------
    # Propriétés
    # -------------------------------------------------------------------------

    @property
    def min_risk_reward(self) -> float:
        """Ratio risque/rendement minimum."""
        return self._min_rr

    @min_risk_reward.setter
    def min_risk_reward(self, value: float) -> None:
        self._min_rr = value

    @property
    def max_spread(self) -> int:
        """Spread maximum autorisé."""
        return self._max_spread

    @max_spread.setter
    def max_spread(self, value: int) -> None:
        self._max_spread = value

    @property
    def authorized_sessions(self) -> list[TradingSession]:
        """Liste des sessions autorisées."""
        return list(self._authorized_sessions)

    @property
    def require_htf_alignment(self) -> bool:
        """Indique si la vérification HTF est active."""
        return self._require_htf_alignment

    @property
    def require_news_filter(self) -> bool:
        """Indique si le filtre de news est actif."""
        return self._require_news_filter

    # -------------------------------------------------------------------------
    # Validation principale
    # -------------------------------------------------------------------------

    def validate(
        self,
        signal: Signal,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        spread: int | None = None,
        has_high_impact_news: bool = False,
    ) -> ValidationResult:
        """
        Valide un signal en vérifiant toutes les conditions.

        Args:
            signal: Le signal à valider
            candles: Liste des bougies (du plus ancien au plus récent)
            smc_data: Détections SMC sur le timeframe courant
            htf_smc_data: Détections SMC sur le timeframe supérieur (optionnel).
                Si fourni, la tendance HTF est dérivée de ces données.
            htf_trend: Tendance HTF explicite ("bullish" ou "bearish")
                (optionnel). Prioritaire sur htf_smc_data.
            spread: Spread actuel en points. Utilise le spread de la dernière
                bougie si None.
            has_high_impact_news: True s'il y a des news à impact élevé.

        Returns:
            ValidationResult contenant is_valid, score, failed_conditions,
            explanation.
        """
        logger.info(
            "Validation démarrée | %s | %s | stratégie=%s | confiance=%.2f | R/R=%.2f",
            signal.symbol,
            signal.direction.value,
            signal.strategy_name,
            signal.confidence,
            signal.risk_reward_ratio,
        )

        direction = signal.direction
        direction_str = "bullish" if direction == Direction.BUY else "bearish"
        current_spread = (
            spread if spread is not None else (candles[-1].spread if candles else 0)
        )

        checked: dict[str, bool] = {}
        failed: list[str] = []
        details: dict[str, str] = {}

        # --- 1. Tendance HTF ---
        ok, detail = self._check_htf_trend(
            direction_str, smc_data, htf_smc_data, htf_trend
        )
        checked[COND_HTF_TREND] = ok
        details[COND_HTF_TREND] = detail
        self._log_condition(COND_HTF_TREND, ok, detail)
        if not ok:
            failed.append(COND_HTF_TREND)

        # --- 2. BOS valide ---
        ok, detail = self._check_bos(smc_data, direction_str)
        checked[COND_BOS] = ok
        details[COND_BOS] = detail
        self._log_condition(COND_BOS, ok, detail)
        if not ok:
            failed.append(COND_BOS)

        # --- 3. CHoCH valide ---
        ok, detail = self._check_choch(smc_data, direction_str)
        checked[COND_CHOCH] = ok
        details[COND_CHOCH] = detail
        self._log_condition(COND_CHOCH, ok, detail)
        if not ok:
            failed.append(COND_CHOCH)

        # --- 4. Order Block valide ---
        ok, detail = self._check_order_block(smc_data, direction_str)
        checked[COND_ORDER_BLOCK] = ok
        details[COND_ORDER_BLOCK] = detail
        self._log_condition(COND_ORDER_BLOCK, ok, detail)
        if not ok:
            failed.append(COND_ORDER_BLOCK)

        # --- 5. FVG valide ---
        ok, detail = self._check_fvg(smc_data, direction_str)
        checked[COND_FVG] = ok
        details[COND_FVG] = detail
        self._log_condition(COND_FVG, ok, detail)
        if not ok:
            failed.append(COND_FVG)

        # --- 6. Liquidity Sweep confirmé ---
        ok, detail = self._check_liquidity_sweep(smc_data, direction_str)
        checked[COND_LIQUIDITY_SWEEP] = ok
        details[COND_LIQUIDITY_SWEEP] = detail
        self._log_condition(COND_LIQUIDITY_SWEEP, ok, detail)
        if not ok:
            failed.append(COND_LIQUIDITY_SWEEP)

        # --- 7. Premium / Discount correct ---
        ok, detail = self._check_premium_discount(smc_data, direction)
        checked[COND_PREMIUM_DISCOUNT] = ok
        details[COND_PREMIUM_DISCOUNT] = detail
        self._log_condition(COND_PREMIUM_DISCOUNT, ok, detail)
        if not ok:
            failed.append(COND_PREMIUM_DISCOUNT)

        # --- 8. Session autorisée ---
        ok, detail = self._check_session(candles)
        checked[COND_SESSION] = ok
        details[COND_SESSION] = detail
        self._log_condition(COND_SESSION, ok, detail)
        if not ok:
            failed.append(COND_SESSION)

        # --- 9. Spread acceptable ---
        ok, detail = self._check_spread(current_spread)
        checked[COND_SPREAD] = ok
        details[COND_SPREAD] = detail
        self._log_condition(COND_SPREAD, ok, detail)
        if not ok:
            failed.append(COND_SPREAD)

        # --- 10. News filter ---
        ok, detail = self._check_news(has_high_impact_news)
        checked[COND_NEWS] = ok
        details[COND_NEWS] = detail
        self._log_condition(COND_NEWS, ok, detail)
        if not ok:
            failed.append(COND_NEWS)

        # --- 11. RR minimum ---
        ok, detail = self._check_rr(signal.risk_reward_ratio)
        checked[COND_RR] = ok
        details[COND_RR] = detail
        self._log_condition(COND_RR, ok, detail)
        if not ok:
            failed.append(COND_RR)

        # Calcul du score
        total = len(checked)
        passed = sum(1 for v in checked.values() if v)
        score = passed / total if total > 0 else 0.0
        is_valid = len(failed) == 0

        # Explication textuelle
        if is_valid:
            explanation = (
                f"Signal VALIDÉ | {signal.symbol} | {signal.direction.value} | "
                f"score={score:.2f} | {passed}/{total} conditions | "
                f"stratégie={signal.strategy_name}"
            )
        else:
            explanation = (
                f"Signal REJETÉ | {signal.symbol} | {signal.direction.value} | "
                f"score={score:.2f} | {passed}/{total} conditions | "
                f"échecs={failed} | stratégie={signal.strategy_name}"
            )

        logger.info(explanation)

        return ValidationResult(
            is_valid=is_valid,
            score=score,
            failed_conditions=failed,
            explanation=explanation,
            checked_conditions=checked,
            details=details,
        )

    # -------------------------------------------------------------------------
    # Vérifications individuelles
    # -------------------------------------------------------------------------

    def _check_htf_trend(
        self,
        direction_str: str,
        smc_data: list[dict],
        htf_smc_data: list[dict] | None,
        htf_trend: str | None,
    ) -> tuple[bool, str]:
        """
        Vérifie l'alignement de la tendance HTF avec la direction du signal.

        Priorité :
        1. Tendance HTF explicite (``htf_trend``)
        2. Tendance dérivée des détections SMC HTF (``htf_smc_data``)
        3. Tendance dérivée des détections SMC courantes (``smc_data``)
        """
        if not self._require_htf_alignment:
            return True, "Vérification HTF désactivée"

        # 1. Tendance explicite
        if htf_trend is not None:
            if htf_trend == direction_str:
                return True, f"Tendance HTF alignée ({htf_trend})"
            return False, (
                f"Tendance HTF non alignée (htf={htf_trend}, signal={direction_str})"
            )

        # 2. Détections SMC HTF ou courantes
        source_data = htf_smc_data if htf_smc_data is not None else smc_data
        source_label = "HTF" if htf_smc_data is not None else "courant"

        trend = self._derive_trend(source_data)
        if trend is None:
            return False, f"Aucune tendance détectable dans les données {source_label}"

        if trend == direction_str:
            return True, f"Tendance {source_label} alignée ({trend})"
        return False, (
            f"Tendance {source_label} non alignée "
            f"(tendance={trend}, signal={direction_str})"
        )

    def _derive_trend(self, smc_data: list[dict]) -> str | None:
        """
        Déduit la tendance à partir des détections SMC.

        Utilise le dernier BOS (incluant internal/external) ou MSS pour
        déterminer la direction de la tendance.
        """
        structure_detections = [
            d
            for d in smc_data
            if d.get("concept")
            in (
                SMCConcept.BOS.value,
                SMCConcept.INTERNAL_BOS.value,
                SMCConcept.EXTERNAL_BOS.value,
                SMCConcept.MSS.value,
            )
        ]
        if not structure_detections:
            return None

        # Prendre la détection avec l'index le plus élevé (la plus récente)
        latest = max(structure_detections, key=lambda d: d.get("index", 0))
        return latest.get("direction")

    def _check_bos(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """Vérifie qu'un BOS valide existe dans la direction du signal."""
        bos_detections = [
            d
            for d in smc_data
            if d.get("concept")
            in (
                SMCConcept.BOS.value,
                SMCConcept.INTERNAL_BOS.value,
                SMCConcept.EXTERNAL_BOS.value,
            )
            and d.get("direction") == direction_str
        ]
        if bos_detections:
            latest = max(bos_detections, key=lambda d: d.get("index", 0))
            return True, (
                f"BOS valide ({latest['concept']}, index={latest.get('index')})"
            )
        return False, f"Aucun BOS dans la direction {direction_str}"

    def _check_choch(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """
        Vérifie la validité du CHoCH.

        Règle : un CHoCH dans la direction **opposée** du signal invalide le
        signal. Un CHoCH dans la direction du signal est un plus (retournement
        confirmé). Si aucun CHoCH n'est présent, la condition est validée (pas
        de conflit).
        """
        opposite = "bearish" if direction_str == "bullish" else "bullish"
        opposite_choch = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.CHOCH.value
            and d.get("direction") == opposite
        ]
        if opposite_choch:
            return False, (
                f"CHoCH contradictoire détecté (direction={opposite})"
            )

        same_choch = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.CHOCH.value
            and d.get("direction") == direction_str
        ]
        if same_choch:
            return True, f"CHoCH valide dans la direction {direction_str}"

        return True, "Aucun CHoCH contradictoire"

    def _check_order_block(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """
        Vérifie qu'un Order Block valide existe dans la direction du signal.

        Un OB valide peut être mitigé (le prix est revenu le tester) ou non
        mitigé. Les Breaker Blocks ne sont pas considérés comme des OB valides.
        """
        ob_detections = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.ORDER_BLOCK.value
            and d.get("direction") == direction_str
        ]
        if ob_detections:
            latest = max(ob_detections, key=lambda d: d.get("index", 0))
            mitigated = latest.get("details", {}).get("mitigated", False)
            if mitigated:
                return True, (
                    f"Order Block valide et mitigé (index={latest.get('index')})"
                )
            return True, (
                f"Order Block valide non mitigé (index={latest.get('index')})"
            )
        return False, f"Aucun Order Block dans la direction {direction_str}"

    def _check_fvg(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """Vérifie qu'un FVG valide existe dans la direction du signal."""
        fvg_detections = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.FVG.value
            and d.get("direction") == direction_str
        ]
        if fvg_detections:
            latest = max(fvg_detections, key=lambda d: d.get("index", 0))
            gap_size = latest.get("details", {}).get("gap_size", 0)
            return True, (
                f"FVG valide (index={latest.get('index')}, gap_size={gap_size})"
            )
        return False, f"Aucun FVG dans la direction {direction_str}"

    def _check_liquidity_sweep(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """Vérifie qu'un Liquidity Sweep confirmé existe dans la direction du signal."""
        sweep_detections = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.LIQUIDITY_SWEEP.value
            and d.get("direction") == direction_str
        ]
        if sweep_detections:
            latest = max(sweep_detections, key=lambda d: d.get("index", 0))
            sweep_type = latest.get("details", {}).get("type", "unknown")
            return True, (
                f"Liquidity Sweep confirmé "
                f"(index={latest.get('index')}, type={sweep_type})"
            )
        return False, f"Aucun Liquidity Sweep dans la direction {direction_str}"

    def _check_premium_discount(
        self, smc_data: list[dict], direction: Direction
    ) -> tuple[bool, str]:
        """
        Vérifie que le prix est dans la zone correcte :
        - BUY  → zone **discount** (lower half)
        - SELL → zone **premium** (upper half)
        """
        pd_detections = [
            d for d in smc_data if d.get("concept") == SMCConcept.PREMIUM_DISCOUNT.value
        ]
        if not pd_detections:
            # Essayer avec les détections PREMIUM et DISCOUNT individuelles
            premium = [
                d for d in smc_data if d.get("concept") == SMCConcept.PREMIUM.value
            ]
            discount = [
                d for d in smc_data if d.get("concept") == SMCConcept.DISCOUNT.value
            ]
            if not premium and not discount:
                return False, "Aucune donnée Premium/Discount disponible"

            if direction == Direction.BUY:
                in_discount = any(
                    d.get("details", {}).get("in_discount", False) for d in discount
                )
                if in_discount:
                    return True, "Prix en zone discount (achat autorisé)"
                return False, "Prix hors zone discount (achat non autorisé)"
            in_premium = any(
                d.get("details", {}).get("in_premium", False) for d in premium
            )
            if in_premium:
                return True, "Prix en zone premium (vente autorisée)"
            return False, "Prix hors zone premium (vente non autorisée)"

        latest_pd = max(pd_detections, key=lambda d: d.get("index", 0))
        current_zone = latest_pd.get("details", {}).get("current_zone", "unknown")

        if direction == Direction.BUY:
            if current_zone == "discount":
                return True, "Prix en zone discount (achat autorisé)"
            return False, (
                f"Prix en zone {current_zone} (achat nécessite discount)"
            )
        if current_zone == "premium":
            return True, "Prix en zone premium (vente autorisée)"
        return False, f"Prix en zone {current_zone} (vente nécessite premium)"

    def _check_session(self, candles: list[Candle]) -> tuple[bool, str]:
        """Vérifie que l'heure actuelle tombe dans une session autorisée."""
        if not candles:
            return False, "Aucune bougie pour vérifier la session"

        last_candle = candles[-1]
        session = self._session_detector.get_session_for_time(last_candle.time)

        if session is None:
            return False, (
                f"Aucune session active (heure={last_candle.time.isoformat()})"
            )

        if session.session in self._authorized_sessions:
            return True, f"Session autorisée ({session.name})"
        return False, f"Session non autorisée ({session.name})"

    def _check_spread(self, spread: int) -> tuple[bool, str]:
        """Vérifie que le spread est acceptable."""
        if spread <= self._max_spread:
            return True, f"Spread acceptable ({spread} <= {self._max_spread})"
        return False, f"Spread trop élevé ({spread} > {self._max_spread})"

    def _check_news(self, has_high_impact_news: bool) -> tuple[bool, str]:
        """Vérifie le filtre de news (pas de news à impact élevé)."""
        if not self._require_news_filter:
            return True, "Filtre news désactivé"
        if has_high_impact_news:
            return False, "News à impact élevé détectée — trade bloqué"
        return True, "Aucune news à impact élevé"

    def _check_rr(self, rr: float) -> tuple[bool, str]:
        """Vérifie que le ratio risque/rendement respecte le minimum."""
        if rr >= self._min_rr:
            return True, f"R/R suffisant ({rr:.2f} >= {self._min_rr:.2f})"
        return False, f"R/R insuffisant ({rr:.2f} < {self._min_rr:.2f})"

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _log_condition(
        self, condition: str, passed: bool, detail: str
    ) -> None:
        """Journalise le résultat d'une condition."""
        status = "VALIDÉE" if passed else "ÉCHOUÉE"
        logger.info(
            "Condition %s | %s | %s",
            condition,
            status,
            detail,
        )