"""
Constructeur de contexte marché (MTF) — analyse multi-timeframe du TradingEngine.

Extrait de ``application/trading_engine._analyze_multitimeframe`` sans changer
le comportement. Cette classe encapsule :

1. La détection de la tendance maître (H1) via ``MasterTrendAnalyzer`` ;
2. Le cache H1 (structure recalculée une seule fois par bougie H1 fermée) ;
3. La détection SMC sur le timeframe d'entrée (M5) et sur le H1 ;
4. Le contexte supérieur H4 (tendance et structure) ;
5. La construction du ``MarketContext`` centralisé.

Elle dépend de deux injectables : un détecteur SMC (``smc_detector``) et un
fournisseur de bougies pour d'autres timeframes (``download_data``, ex. H4).

Aucune décision de trading n'est prise ici : le ``MarketContext`` retourné est
consommé ensuite par le ``SignalGenerator`` et les garde-fous d'exécution.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from arty_trading.core.entities import Candle
from arty_trading.core.enums import LogCategory, TimeFrame
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.SYSTEM)

# Signature d'un fournisseur de bougies asynchrone (symbol, timeframe) -> list[Candle]
DownloadData = Callable[[str, TimeFrame], Awaitable[list[Candle] | None]]


class MarketContextBuilder:
    """Construit le ``MarketContext`` MTF (H4 contexte, H1 biais, M5 entrée)."""

    def __init__(
        self,
        smc_detector: Any,
        download_data: DownloadData,
    ) -> None:
        """
        Args:
            smc_detector: Détecteur SMC (``ISMCDetector``) pour H1/M5.
            download_data: Récupère des bougies (utilisé pour le H4 informatif).
        """
        self._smc_detector = smc_detector
        self._download_data = download_data

        # Cache H1 : la structure H1 est recalculée une seule fois par bougie H1
        # fermée. Le cache est invalidé uniquement sur nouvelle bougie H1.
        self._htf_cache: dict[str, Any] = {}
        self._h4_cache: dict[str, Any] = {}

    async def build(
        self,
        symbol: str,
        htf_candles: list[Candle],
        ltf_candles: list[Candle],
        setup_tf_candles: list[Candle] | None = None,
    ) -> Any | None:
        """
        Analyse multi-timeframe et construit le ``MarketContext``.

        Subs-étapes (identiques à l'ancien ``_analyze_multitimeframe``) :
        1. Tendance maître H1 (avec cache interne).
        2. Détection SMC H1 et M5.
        3. Contexte macro H4 (informatif, loggé).
        4. Construction du ``MarketContext``.
        5. (Phase 3) Détection SMC M15 (setup) + tendance M15 dérivée du
           dernier BOS/CHoCH M15. Le M15 ne modifie JAMAIS le biais H1.

        Args:
            symbol: Symbole à analyser.
            htf_candles: Bougies du timeframe supérieur (1H).
            ltf_candles: Bougies du timeframe d'entrée (5M).
            setup_tf_candles: Bougies du timeframe de setup (15M), optionnel.
                Si None, les champs M15 du contexte restent vides/neutres
                (rétro-compatibilité).

        Returns:
            ``MarketContext`` ou ``None`` si l'analyse échoue.
        """
        from arty_trading.modules.decision.master_trend import MasterTrendAnalyzer

        trend_analyzer = MasterTrendAnalyzer(htf=TimeFrame.H1)

        # Cache H1 : ne recalculer que si nouvelle bougie H1.
        htf_key = f"{symbol}_H1"
        latest_htf = htf_candles[-1] if htf_candles else None
        cached = self._htf_cache.get(htf_key)
        if cached and latest_htf and cached.get("time") == latest_htf.time:
            master_trend = cached["master_trend"]
            htf_smc_data = cached["htf_smc_data"]
            logger.debug(
                "H1 cache hit | %s | tendance=%s", symbol, master_trend,
            )
        else:
            master_trend = trend_analyzer.get_master_trend(htf_candles)

            htf_smc_data: list[dict] = []
            try:
                htf_smc_data = await self._smc_detector.detect(htf_candles, symbol)
            except Exception as exc:
                logger.error("Erreur analyse SMC H1 | %s | %s", symbol, exc)

            if latest_htf:
                self._htf_cache[htf_key] = {
                    "time": latest_htf.time,
                    "master_trend": master_trend,
                    "htf_smc_data": htf_smc_data,
                }

        ltf_smc_data: list[dict] = []
        try:
            ltf_smc_data = await self._smc_detector.detect(ltf_candles, symbol)
        except Exception as exc:
            logger.error("Erreur analyse SMC M5 | %s | %s", symbol, exc)

        # H4 : contexte supérieur, calculé une seule fois par bougie clôturée.
        h4_trend = "unknown"
        h4_candles: list[Candle] = []
        h4_smc_data: list[dict] = []
        try:
            h4_candles = await self._download_data(symbol, TimeFrame.H4) or []
            if h4_candles and len(h4_candles) >= 5:
                latest_h4 = h4_candles[-1]
                cached_h4 = self._h4_cache.get(f"{symbol}_H4")
                if cached_h4 and cached_h4.get("time") == latest_h4.time:
                    h4_trend = cached_h4["h4_trend"]
                    h4_smc_data = cached_h4["h4_smc_data"]
                else:
                    h4_trend = trend_analyzer.get_master_trend(h4_candles)
                    h4_smc_data = await self._smc_detector.detect(h4_candles, symbol)
                    self._h4_cache[f"{symbol}_H4"] = {
                        "time": latest_h4.time,
                        "h4_trend": h4_trend,
                        "h4_smc_data": h4_smc_data,
                    }
                logger.info(
                    "[H4 CONTEXTE] %s | H4=%s | detections=%d",
                    symbol, h4_trend, len(h4_smc_data),
                )
        except Exception as exc:
            logger.warning("Erreur analyse H4 | %s | %s", symbol, exc)

        market_context = trend_analyzer.build_market_context(
            symbol=symbol,
            htf_candles=htf_candles,
            ltf_candles=ltf_candles,
            htf_smc_data=htf_smc_data,
            ltf_smc_data=ltf_smc_data,
        )

        # Phase 3 : détection SMC M15 (timeframe de setup).
        # Le M15 fournit les zones de setup et une tendance locale, mais ne
        # modifie jamais le biais H1 (master_trend reste calculé sur H1).
        setup_smc_data: list[dict] = []
        if setup_tf_candles:
            try:
                setup_smc_data = await self._smc_detector.detect(
                    setup_tf_candles, symbol
                )
            except Exception as exc:
                logger.error("Erreur analyse SMC M15 | %s | %s", symbol, exc)

        setup_trend = _derive_setup_trend(setup_smc_data)

        market_context.setup_smc_data = setup_smc_data
        market_context.setup_trend = setup_trend
        market_context.setup_candles = setup_tf_candles or []
        market_context.h4_trend = h4_trend
        market_context.h4_candles = h4_candles
        market_context.h4_smc_data = h4_smc_data
        if setup_tf_candles:
            market_context.setup_tf = setup_tf_candles[-1].timeframe

        logger.info(
            "Analyse MTF | %s | H4=%s | H1=%s | M15=%s (detections=%d) | M5 detections=%d | H1 detections=%d",
            symbol,
            h4_trend,
            market_context.master_trend,
            setup_trend,
            len(setup_smc_data),
            len(ltf_smc_data),
            len(htf_smc_data),
        )

        return market_context


def _derive_setup_trend(setup_smc_data: list[dict]) -> str:
    """
    Dérive la tendance du timeframe de setup (M15) des détections SMC.

    Utilise le dernier événement de structure (BOS/CHoCH/MSS) confirmé sur
    clôture. Retourne "neutral" si aucune détection de structure.
    """
    structure_events = [
        d
        for d in setup_smc_data
        if d.get("concept") in ("bos", "choch", "mss", "internal_bos")
    ]
    if not structure_events:
        return "neutral"

    latest = max(structure_events, key=lambda d: d.get("index", 0))
    direction = latest.get("direction", "neutral")
    return direction if direction in ("bullish", "bearish") else "neutral"