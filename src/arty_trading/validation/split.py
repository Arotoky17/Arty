"""Guard data before passing it to a backtest. UTC half-open periods."""

from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from arty_trading.config.operational import load_config
from arty_trading.core.entities import Candle
from arty_trading.validation.trial_registry import TrialRegistry

T = TypeVar("T")


HOLDOUT_LOADING = ContextVar("arty_holdout_loading", default=False)


class HoldoutAccessError(ValueError):
    pass


class DataSplit:
    def __init__(self, registry: TrialRegistry, config: dict[str, Any] | None = None) -> None:
        self.registry = registry
        self.config = config or load_config("split.yaml")
        self.dev = self._interval("development")
        self.holdout_ready = self.config["holdout"]["end"] is not None
        self.holdout = self._interval("holdout")
        if not (
            self.dev[0] < self.dev[1] <= self.holdout[0]
            and (not self.holdout_ready or self.holdout[0] < self.holdout[1])
        ):
            raise ValueError("Invalid or overlapping split")
        self.purge = self._interval("purge") if "purge" in self.config else None
        if self.purge is not None:
            if self.purge != (self.dev[1], self.holdout[0]):
                raise ValueError("Purge must separate dev and holdout exactly")
            cfg = self.config["purge"]
            day = self.purge[0]
            count = 0
            while day < self.purge[1]:
                if (
                    day.weekday() in cfg["weekdays"]
                    and day.date().isoformat() not in cfg["holidays"]
                ):
                    count += 1
                day += timedelta(days=1)
            if count != cfg["business_days"]:
                raise ValueError("Purge business-day count does not match frozen configuration")

    def _interval(self, key: str) -> tuple[datetime, datetime]:
        row = self.config[key]
        start = datetime.fromisoformat(row["start"])
        end = datetime.fromisoformat(row["end"]) if row["end"] else start
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Split dates require explicit UTC offset")
        return start.astimezone(UTC), end.astimezone(UTC)

    def assert_period_development(self, start: datetime, end: datetime) -> None:
        if not self.dev[0] <= start < end <= self.dev[1]:
            raise HoldoutAccessError("Requested period is outside development")

    @staticmethod
    def batches(data: Any) -> list[list[Candle]]:
        return list(data.values()) if isinstance(data, dict) else [data]

    def assert_development(self, candles: list[Candle]) -> None:
        if any(not self.dev[0] <= c.time.astimezone(UTC) < self.dev[1] for c in candles):
            raise HoldoutAccessError("Non-development data: use load_holdout(setup_id, reason)")

    def load_development(self, candles: list[Candle]) -> list[Candle]:
        return [c for c in candles if self.dev[0] <= c.time.astimezone(UTC) < self.dev[1]]

    def load_holdout(
        self,
        setup_id: str,
        reason: str,
        *,
        enabled: bool = False,
        override: bool = False,
        override_reason: str | None = None,
        loader: Callable[[], T],
    ) -> T:
        setup_id = setup_id.strip()
        reason = reason.strip()
        from arty_trading.validation.preregistration import assert_setup_run_allowed

        assert_setup_run_allowed(setup_id, holdout=True)
        if not self.holdout_ready:
            raise HoldoutAccessError("Holdout unavailable: no imported closed bid/ask data")
        if not enabled or not setup_id or not reason:
            raise HoldoutAccessError("Explicit flag, setup and reason required")
        if override and (not override_reason or not override_reason.strip()):
            raise HoldoutAccessError("Override requires a logged reason")
        # Serialize the count + append to prevent concurrent double access.
        with self.registry.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM holdout_access WHERE setup_id=?", (setup_id,)
            ).fetchone()[0]
            if count and not override:
                raise HoldoutAccessError("Holdout already accessed for this setup")
            db.execute(
                "INSERT INTO holdout_access (timestamp,setup_id,reason,override_reason) "
                "VALUES (?,?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    setup_id,
                    reason,
                    override_reason if override else None,
                ),
            )
        # Access is consumed even if the loader fails: no invisible repeated inspection.
        token = HOLDOUT_LOADING.set(True)
        try:
            candles = loader()
        finally:
            HOLDOUT_LOADING.reset(token)
        if any(
            not self.holdout[0] <= c.time.astimezone(UTC) < self.holdout[1]
            for batch in self.batches(candles)
            for c in batch
        ):
            raise HoldoutAccessError("Loader returned data outside holdout")
        return candles
