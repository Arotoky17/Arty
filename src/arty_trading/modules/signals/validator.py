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
from arty_trading.utils.helpers import is_fresh_structure

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

# Couverture de la logique de validation :
# - Les conditions HARD sont OBLIGATOIRES : un seul échec rejette le signal
#   (aucun trade possible). Ce sont les garde-fous de sécurité.
# - Les conditions CONFLUENCE sont des indicateurs de QUALITÉ comptabilisés :
#   il suffit d'en valider un minimum (``min_confluence_count``) pour que le
#   signal passe. Un signal n'est donc plus rejeté pour un simple concept
#   ICT manquant, tout en restant filtré sur la solidité de la confluence.
HARD_CONDITIONS: tuple[str, ...] = (
    COND_HTF_TREND,
    COND_BOS,
    COND_CHOCH,
    COND_SESSION,
    COND_SPREAD,
    COND_NEWS,
    COND_RR,
)

CONFLUENCE_CONDITIONS: tuple[str, ...] = (
    COND_ORDER_BLOCK,
    COND_FVG,
    COND_LIQUIDITY_SWEEP,
    COND_PREMIUM_DISCOUNT,
)

# Nombre de confluences dont la validation est nécessaire par défaut.
DEFAULT_MIN_CONFLUENCE_COUNT = 2


# =============================================================================
# Résultat de validation
# =============================================================================


@dataclass
class ValidationResult:
    """
    Résultat de la validation d'un signal.

    ``failed_conditions`` ne contient que les conditions **HARD** (bloquantes)
    : tant qu'une seule d'entre elles échoue, ``is_valid`` est False.
    Les confluences optionnelles (FVG, Order Block, Liquidity Sweep,
    Premium/Discount) ne bloquent pas : elles sont comptabilisées dans
    ``confluence_score`` / ``confluence_passed``. Le signal est accepté si
    aucune condition HARD n'échoue **et** si suffisamment de confluences sont
    présentes (``confluence_passed >= min_confluence_count``).

    Attributes:
        is_valid: True si toutes les conditions HARD sont validées ET si le
            nombre minimum de confluences est atteint.
        score: Proportion de conditions validées sur l'ensemble (0.0 à 1.0).
        failed_conditions: Liste des conditions HARD échouées (bloquantes).
        explanation: Résumé textuel du résultat.
        checked_conditions: Détail condition par condition (nom → bool).
        details: Détail textuel par condition (nom → description).
        confluence_score: Proportion de confluences validées (0.0 à 1.0).
        confluence_passed: Nombre de confluences validées (sur 4).
        confluence_total: Nombre total de confluences évaluées (toujours 4).
    """

    is_valid: bool
    score: float
    failed_conditions: list[str] = field(default_factory=list)
    explanation: str = ""
    checked_conditions: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    confluence_score: float = 0.0
    confluence_passed: int = 0
    confluence_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convertit le résultat en dictionnaire."""
        return {
            "is_valid": self.is_valid,
            "score": round(self.score, 4),
            "failed_conditions": list(self.failed_conditions),
            "explanation": self.explanation,
            "checked_conditions": dict(self.checked_conditions),
            "details": dict(self.details),
            "confluence_score": round(self.confluence_score, 4),
            "confluence_passed": self.confluence_passed,
            "confluence_total": self.confluence_total,
        }


# =============================================================================
# Validateur de signaux
# =============================================================================


class SignalValidator:
    """
    Validateur de signaux de trading (SMC/ICT).

    Deux catégories de conditions :
    - **HARD** (bloquantes) : tendance HTF, BOS, absence de CHoCH
      contradictoire, session, spread, news, R/R minimum. Un seul échec
      rejette le signal.
    - **CONFLUENCE** (qualité comptabilisée) : Order Block, FVG, Liquidity
      Sweep, Premium/Discount. Il suffit d'en valider au moins
      ``min_confluence_count`` (défaut 2 sur 4) pour que le signal passe.

    Cela évite qu'un signal solide soit rejeté pour un simple concept ICT
    manquant, tout en gardant les garde-fous de sécurité obligatoires.

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
        min_confluence_count: int = DEFAULT_MIN_CONFLUENCE_COUNT,
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
            min_confluence_count: Nombre minimum de confluences à valider parmi
                les 4 confluences optionnelles (défaut 2 sur 4).
        """
        if not 0 <= min_confluence_count <= len(CONFLUENCE_CONDITIONS):
            raise ValueError(
                f"min_confluence_count doit être entre 0 et "
                f"{len(CONFLUENCE_CONDITIONS)}"
            )
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
        self._min_confluence_count = min_confluence_count

        logger.info(
            "SignalValidator initialisé | min_rr=%.2f | max_spread=%d | "
            "sessions=%s | htf_alignment=%s | news_filter=%s | "
            "min_confluence=%d/%d",
            self._min_rr,
            self._max_spread,
            [s.value for s in self._authorized_sessions],
            self._require_htf_alignment,
            self._require_news_filter,
            self._min_confluence_count,
            len(CONFLUENCE_CONDITIONS),
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

    @property
    def min_confluence_count(self) -> int:
        """Nombre minimum de confluences à valider (parmi les 4 optionnelles)."""
        return self._min_confluence_count

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

        # Calcul du score global (sur l'ensemble des 11 conditions).
        total = len(checked)
        passed = sum(1 for v in checked.values() if v)
        score = passed / total if total > 0 else 0.0

        # Partition : seules les conditions HARD bloquent le trade. Les
        # confluences optionnelles sont comptabilisées avec un seuil minimum.
        hard_failed = [c for c in failed if c in HARD_CONDITIONS]
        confluence_total = sum(1 for c in CONFLUENCE_CONDITIONS if c in checked)
        confluence_passed = confluence_total - sum(
            1 for c in CONFLUENCE_CONDITIONS if c in failed
        )
        hard_ok = len(hard_failed) == 0
        confluence_ok = confluence_passed >= self._min_confluence_count
        is_valid = hard_ok and confluence_ok
        confluence_str = f"{confluence_passed}/{confluence_total}"

        if confluence_total > 0:
            confluence_score = confluence_passed / confluence_total
        else:
            confluence_score = 1.0

        # Explication textuelle
        if is_valid:
            explanation = (
                f"Signal VALIDÉ | {signal.symbol} | {signal.direction.value} | "
                f"score={score:.2f} | {passed}/{total} conditions | "
                f"confluences={confluence_str} (min={self._min_confluence_count}) | "
                f"stratégie={signal.strategy_name}"
            )
        else:
            explanation = (
                f"Signal REJETÉ | {signal.symbol} | {signal.direction.value} | "
                f"score={score:.2f} | {passed}/{total} conditions | "
                f"échecs_hard={hard_failed} | "
                f"confluences={confluence_str} (min={self._min_confluence_count}) | "
                f"stratégie={signal.strategy_name}"
            )

        logger.info(explanation)

        return ValidationResult(
            is_valid=is_valid,
            score=score,
            failed_conditions=hard_failed,
            explanation=explanation,
            checked_conditions=checked,
            details=details,
            confluence_score=confluence_score,
            confluence_passed=confluence_passed,
            confluence_total=confluence_total,
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

        Utilise le dernier BOS (incluant internal/external) ou MSS **frais**
        pour déterminer la direction de la tendance. Un événement trop ancien
        est ignoré pour éviter qu'un BOS obsolète ne valide un signal contre
        la structure actuelle.
        """
        total_candles = max((d.get("index", 0) for d in smc_data), default=0) + 1
        latest = is_fresh_structure(smc_data, total_candles, max_age_bars=20)
        if latest is None:
            return None
        return latest.get("direction")

    def _check_bos(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """Vérifie qu'un BOS valide et frais existe dans la direction du signal."""
        total_candles = max((d.get("index", 0) for d in smc_data), default=0) + 1
        latest = is_fresh_structure(smc_data, total_candles, max_age_bars=20)
        if latest is None:
            return False, "Aucune structure fraîche (BOS/CHoCH/MSS) disponible"
        if latest.get("direction") != direction_str:
            concept = latest.get("concept")
            return False, (
                f"Dernière structure fraîche opposée ({concept} "
                f"{latest.get('direction')})"
            )
        return True, (
            f"BOS valide et frais ({latest['concept']}, index={latest.get('index')})"
        )

    def _check_choch(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """
        Vérifie la validité du CHoCH.

        Règle : un CHoCH **frais** dans la direction **opposée** du signal
        invalide le signal. Un CHoCH frais dans la direction du signal est un
        plus (retournement confirmé). Si aucun CHoCH frais n'est présent, la
        condition est validée (pas de conflit).
        """
        opposite = "bearish" if direction_str == "bullish" else "bullish"
        total_candles = max((d.get("index", 0) for d in smc_data), default=0) + 1

        opposite_choch = []
        same_choch = []
        for d in smc_data:
            if d.get("concept") != SMCConcept.CHOCH.value:
                continue
            age = total_candles - 1 - d.get("index", 0)
            if age > 20:
                continue
            if d.get("direction") == opposite:
                opposite_choch.append(d)
            elif d.get("direction") == direction_str:
                same_choch.append(d)

        if opposite_choch:
            return False, (
                f"CHoCH contradictoire détecté (direction={opposite})"
            )

        if same_choch:
            latest_same = max(same_choch, key=lambda d: d.get("index", 0))
            idx = latest_same.get("index")
            return True, (
                f"CHoCH valide dans la direction {direction_str} (index={idx})"
            )

        return True, "Aucun CHoCH contradictoire"

    def _check_order_block(
        self, smc_data: list[dict], direction_str: str
    ) -> tuple[bool, str]:
        """
        Vérifie qu'un Order Block valide existe dans la direction du signal.

        Un OB valide peut être mitigé (le prix est revenu le tester) ou non
        mitigé. Les Breaker Blocks ne sont pas considérés comme des OB valides.

        Un Order Block est rejeté si le nombre de mitigations dépasse le
        seuil maximal (zone considérée comme épuisée).
        """
        ob_detections = [
            d
            for d in smc_data
            if d.get("concept") == SMCConcept.ORDER_BLOCK.value
            and d.get("direction") == direction_str
        ]
        if ob_detections:
            latest = max(ob_detections, key=lambda d: d.get("index", 0))
            mitigation_count = latest.get("details", {}).get("mitigation_count", 0)
            max_mitigations = 2
            if mitigation_count > max_mitigations:
                return False, (
                    f"Order Block épuisé (mitigations={mitigation_count}, max={max_mitigations})"
                )
            return True, (
                f"Order Block valide (mitigations={mitigation_count}, index={latest.get('index')})"
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
