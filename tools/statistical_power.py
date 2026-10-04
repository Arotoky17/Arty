"""Planning-only power calculation; no market data, trading or P&L reads."""

from pathlib import Path
from statistics import NormalDist

from arty_trading.config.operational import load_config
from arty_trading.validation.market_data import write_report


def planning_power() -> dict:
    cfg = load_config("setup1_preregistration.yaml")["statistical_power"]
    normal = NormalDist()
    factor = normal.inv_cdf((1 + cfg["confidence"]) / 2) + normal.inv_cdf(cfg["power"])
    estimates = {}
    for name in ("dev", "holdout"):
        n = cfg[f"expected_{name}_trades"]
        estimates[name] = {
            "assumed_trades": n,
            "minimum_detectable_expectancy_r": (
                factor
                * cfg["assumed_trade_r_standard_deviation"]
                * (cfg["assumed_design_effect"] / n) ** 0.5
            ),
        }
    return {
        "assumptions": cfg,
        "estimates": estimates,
        "pnl_inspected": False,
        "limitation": "Planning approximation, not a measured trade count or bootstrap guarantee",
    }


if __name__ == "__main__":
    output = Path("reports/prebaseline/statistical_power.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = planning_power()
    write_report(output, report)
    print(report["estimates"])
