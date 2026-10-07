"""Shared closed-bar exit priority for the limit Setup 1, replay and observation."""

from arty_trading.core.enums import Direction


def closed_bar_exit(signal, bar, *, newly_filled, stop_on_fill_bar, remaining_bars, ny_flat=False):
    buy = signal.direction == Direction.BUY
    stop, target = float(signal.stop_loss), float(signal.take_profit)
    stop_hit = float(bar.low) <= stop if buy else float(bar.high) >= stop
    target_hit = float(bar.high) >= target if buy else float(bar.low) <= target
    if stop_hit and (not newly_filled or stop_on_fill_bar):
        price = min(float(bar.open), stop) if buy else max(float(bar.open), stop)
        return "stop", price, "stop"
    if target_hit and not newly_filled:
        return "target_2R", target, "limit"
    if ny_flat:
        return "ny_17h_flat", float(bar.close), "market"
    if remaining_bars <= 0:
        return "holding_48_market_bars", float(bar.close), "market"
    return None
