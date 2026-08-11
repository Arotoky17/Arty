"""
Contexte marché centralisé pour l'analyse multi-timeframe.

Centralise toutes les informations de marché nécessaires aux décisions
de trading, évitant que chaque stratégie calcule une vision différente
de la tendance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame


@dataclass
class MarketContext:
    """
    Contexte marché centralisé pour une paire et un instant donné.

    Fournit une vision unifiée du marché à partir de l'analyse multi-timeframe,
    sans duplication de calculs entre les stratégies et le moteur de décision.
    """

    symbol: str
    timestamp: datetime
    htf: TimeFrame = TimeFrame.H1
    ltf: TimeFrame = TimeFrame.M5
    master_trend: str = "neutral"
    trend_confidence: float = 0.0
    hh: Decimal | None = None
    hl: Decimal | None = None
    lh: Decimal | None = None
    ll: Decimal | None = None
    recent_bos: list[dict] = field(default_factory=list)
    recent_choch: list[dict] = field(default_factory=list)
    recent_mss: list[dict] = field(default_factory=list)
    swing_high: Decimal | None = None
    swing_low: Decimal | None = None
    premium: Decimal | None = None
    discount: Decimal | None = None
    liquidity_zones: list[dict] = field(default_factory=list)
    key_levels: list[dict] = field(default_factory=list)
    active_order_blocks: list[dict] = field(default_factory=list)
    active_fvgs: list[dict] = field(default_factory=list)
    spread: int = 0
    atr: Decimal = Decimal("0")
    entry_confirmation: str = "none"
    htf_candles: list[Candle] = field(default_factory=list)
    ltf_candles: list[Candle] = field(default_factory=list)
    htf_smc_data: list[dict] = field(default_factory=list)
    ltf_smc_data: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_bullish(self) -> bool:
        return self.master_trend == "bullish"

    def is_bearish(self) -> bool:
        return self.master_trend == "bearish"

    def is_neutral(self) -> bool:
        return self.master_trend == "neutral"

    def allows_buy(self) -> bool:
        return self.is_bullish()

    def allows_sell(self) -> bool:
        return self.is_bearish()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "htf": self.htf.value,
            "ltf": self.ltf.value,
            "master_trend": self.master_trend,
            "trend_confidence": self.trend_confidence,
            "hh": float(self.hh) if self.hh else None,
            "hl": float(self.hl) if self.hl else None,
            "lh": float(self.lh) if self.lh else None,
            "ll": float(self.ll) if self.ll else None,
            "recent_bos_count": len(self.recent_bos),
            "recent_choch_count": len(self.recent_choch),
            "recent_mss_count": len(self.recent_mss),
            "swing_high": float(self.swing_high) if self.swing_high else None,
            "swing_low": float(self.swing_low) if self.swing_low else None,
            "premium": float(self.premium) if self.premium else None,
            "discount": float(self.discount) if self.discount else None,
            "spread": self.spread,
            "atr": float(self.atr),
            "entry_confirmation": self.entry_confirmation,
            "allows_buy": self.allows_buy(),
            "allows_sell": self.allows_sell(),
        }
