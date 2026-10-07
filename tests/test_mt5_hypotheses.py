from datetime import UTC, datetime

from arty_trading.validation.mt5_hypotheses import (
    HYPOTHESES,
    compare_hypotheses,
    select_year,
    to_local,
    to_utc,
    transition_sensitive,
)


def test_three_policies_differ_on_dst_divergence_and_winter():
    march = datetime(2020, 3, 20, 12, tzinfo=UTC)
    assert to_local(march, "eet_eu_dst").hour == 14
    assert to_local(march, "eet_us_dst").hour == 15
    winter = datetime(2020, 1, 20, 12, tzinfo=UTC)
    assert to_local(winter, "utc_plus_3_fixed").hour == 15
    assert to_local(winter, "eet_us_dst").hour == 14
    autumn = datetime(2020, 10, 28, 12, tzinfo=UTC)
    assert to_local(autumn, "eet_eu_dst").hour == 14
    assert to_local(autumn, "eet_us_dst").hour == 15
    assert transition_sensitive(datetime(2020, 3, 20, 12))
    assert not transition_sensitive(datetime(2020, 7, 20, 12))
    for hypothesis in HYPOTHESES:
        assert to_utc(to_local(march, hypothesis), hypothesis) == (march, None)


def test_us_shift_resolves_actual_dst_gap_and_fold():
    assert to_utc(datetime(2020, 3, 8, 9, 30), "eet_us_dst")[1] == "nonexistent_dst"
    assert to_utc(datetime(2020, 11, 1, 8, 30), "eet_us_dst")[1] == "ambiguous_dst"


def test_selection_requires_margin_and_evidence():
    def row(hypothesis, score, count=6):
        return {
            "hypothesis": hypothesis,
            "score_minutes": score,
            "transition": {
                "components": {"pause": {"samples": count}, "reopen": {"samples": count}}
            },
            "all": {"components": {"news": {"samples": count}}},
        }

    rows = [row(name, value) for name, value in zip(HYPOTHESES, [1, 30, 40], strict=True)]
    assert select_year(rows)["selected"] == "eet_eu_dst"
    rows[1]["score_minutes"] = 5
    assert select_year(rows)["selected"] is None
    rows[1]["score_minutes"] = 30
    rows[0]["all"]["components"]["news"]["samples"] = 1
    assert select_year(rows)["selected"] is None


def test_offline_comparison_retains_ambiguous_year_and_evidence(tmp_path):
    source = tmp_path / "export.csv"
    source.write_text(
        "Date,Time,Open,High,Low,Close,TickVol,Vol,Spread\n"
        "2020.03.20,23:59:00,100,101,99,100,10,0,20\n"
        "2020.03.21,01:00:00,100,101,99,100,10,0,20\n"
        "2020.03.20,16:30:00,100,101,99,100,1000,0,20\n"
    )
    news = tmp_path / "news.csv"
    news.write_text(
        "time_utc,type,status,source_url\n"
        "2020-03-20T13:30:00+00:00,NFP,released,https://example.test\n"
    )
    report = compare_hypotheses(source, news)
    assert report["selected_hypothesis"] is None
    assert report["ambiguous_years"] == ["2020"]
    assert report["records"]["eet_us_dst"][-1]["delta_minutes"] == 0
    assert report["records"]["eet_eu_dst"][-1]["delta_minutes"] == 60
