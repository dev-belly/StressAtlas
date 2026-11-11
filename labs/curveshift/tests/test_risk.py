"""Independent financial expectations, time boundaries and API contracts."""

import base64
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import polars as pl
import pytest
import QuantLib as ql
from fastapi.testclient import TestClient
from nelson_siegel_svensson import NelsonSiegelSvenssonCurve

from curveshift.api import app
from curveshift.cli import evaluate_scenario, main
from curveshift.engine import (
    BondSpec,
    build_book,
    cashflow_price,
    key_basis,
    price_bond,
    quantlib_price,
    scenario,
    sensitivities,
)
from curveshift.market import check_reported_yields, load_market, select_curve
from curveshift.report import analyze, build_packet, verify_report, write_report
from curveshift.research import duration_neutral_case, historical_risk, pca_study, tail_risk


@pytest.fixture(scope="module")
def market():
    return load_market()


@pytest.fixture(scope="module")
def current(market):
    row, curve = select_curve(market[0], "2025-12-31")
    return build_book(row["Date"].isoformat()), curve


@pytest.fixture(scope="module")
def result():
    return analyze()


def test_zero_coupon_closed_form_and_convexity():
    flat = NelsonSiegelSvenssonCurve(0.04, 0, 0, 0, 1, 2)
    bond = build_book("2025-12-31", [BondSpec("zero", 10, 0, 1_000_000)])[0]
    t = bond["times"][-1]
    expected = 1_000_000 * math.exp(-0.04 * t)
    assert price_bond(bond, flat) == pytest.approx(expected, abs=1e-7)
    assert quantlib_price(bond, "2025-12-31", flat) == pytest.approx(expected, abs=1e-7)
    risk = sensitivities(bond, flat)
    assert risk["effective_duration"] == pytest.approx(t)
    assert risk["convexity"] == pytest.approx(t * t)
    assert price_bond(bond, flat, [50] * 4) == pytest.approx(expected * math.exp(-0.005 * t), abs=1e-7)


def test_two_year_cashflows_have_four_coupons_and_one_principal():
    bond = build_book("2025-12-31", [BondSpec("two", 2, 0.04, 100)])[0]
    assert bond["dates"] == ["2026-06-30", "2026-12-31", "2027-06-30", "2027-12-31", "2027-12-31"]
    assert bond["amounts"] == pytest.approx([2, 2, 2, 2, 100])
    t = np.array([181 / 365, 1, 546 / 365, 2, 2])
    assert bond["times"] == pytest.approx(t)
    assert cashflow_price(t, bond["amounts"], np.full(5, 0.03)) == pytest.approx(
        sum(a * math.exp(-0.03 * x) for a, x in zip([2, 2, 2, 2, 100], t, strict=True))
    )


def test_key_partition_and_endpoint_rule():
    times = np.array([0.5, 1, 2, 3, 5, 8, 10, 20, 30, 40])
    basis = key_basis(times)
    assert basis.sum(axis=1) == pytest.approx(np.ones(len(times)))
    assert (basis >= 0).all()
    assert basis[0] == pytest.approx([1, 0, 0, 0])
    assert basis[-1] == pytest.approx([0, 0, 0, 1])


@pytest.mark.parametrize("shock", [[0] * 4, [50] * 4, [-100] * 4, [0, 25, 50, 100], [-30, 0, 10, 0]])
def test_quantlib_and_discount_sum_agree(current, shock):
    book, curve = current
    for bond in book:
        assert price_bond(bond, curve, shock) == pytest.approx(
            quantlib_price(bond, "2025-12-31", curve, shock), abs=1e-6
        )


def test_analytic_dv01_and_key_additivity(current):
    book, curve = current
    for bond in book:
        risk = sensitivities(bond, curve)
        central = (
            quantlib_price(bond, "2025-12-31", curve, [-1] * 4) - quantlib_price(bond, "2025-12-31", curve, [1] * 4)
        ) / 2
        assert sum(risk["key_dv01"]) == pytest.approx(risk["parallel_dv01"], abs=1e-9)
        assert risk["parallel_dv01"] == pytest.approx(central, rel=3e-6)


def test_linear_risk_cancels_but_twist_loss_remains(current):
    hedge = duration_neutral_case(*current)
    assert abs(hedge["parallel_dv01_usd"]) < 1e-8
    assert hedge["gross_face_usd"] > hedge["long_10y_face_usd"]
    assert hedge["outcomes"][1]["full_pnl_usd"] < -90_000
    assert hedge["outcomes"][0]["full_pnl_usd"] > 1000


def test_second_order_improves_the_parallel_approximation(current):
    result = scenario(*current, [100] * 4)
    assert abs(result["full_pnl"] - result["second_order_pnl"]) < abs(result["full_pnl"] - result["linear_pnl"])


@pytest.mark.parametrize("bad", [[float("nan")] * 4, [float("inf")] * 4, [301, 0, 0, 0], [0, 0, 0], []])
def test_invalid_shocks_fail(current, bad):
    with pytest.raises(ValueError):
        scenario(*current, bad)


def test_exact_empirical_tail_probability_mass():
    result = tail_risk([1, 2, 3, 4], 0.625)
    assert result["var_usd"] == 3
    assert result["es_usd"] == pytest.approx(11 / 3)
    assert tail_risk([2, 2, 2, 2], 0.99)["es_usd"] == pytest.approx(2)
    assert tail_risk([-4, -3, -2, -1], 0.5)["es_usd"] == pytest.approx(-1.5)


@pytest.mark.parametrize("losses,confidence", [([], 0.99), ([float("nan")], 0.99), ([1], 0), ([1], 1)])
def test_invalid_tail_samples_fail(losses, confidence):
    with pytest.raises(ValueError):
        tail_risk(losses, confidence)


def test_frozen_source_and_published_yield_reconstruction(market):
    frame, source = market
    assert frame.height == 2499
    assert source["start"] == "2016-01-04" and source["end"] == "2025-12-31"
    assert source["excluded_dates"]
    assert source["mirror_commit"] == "26e50238532cea42ef594d91189082a56425273d"
    assert check_reported_yields(frame)["max_error_bp"] < 0.006


def test_date_rollback_is_explicit_and_outside_window_fails(market):
    row, _ = select_curve(market[0], "2025-06-29")
    assert row["Date"].isoformat() == "2025-06-27"
    for cutoff in ["2010-01-01", "2026-01-01", "2025-02-30"]:
        with pytest.raises(ValueError):
            select_curve(market[0], cutoff)


def test_pca_training_is_not_affected_by_heldout_changes(market):
    frame = market[0]
    original = pca_study(frame)
    changed = frame.with_columns(
        pl.when(pl.col("Date").dt.year() == 2025)
        .then(pl.col("SVENY30") + 2)
        .otherwise(pl.col("SVENY30"))
        .alias("SVENY30")
    )
    altered = pca_study(changed)
    assert altered["components"] == pytest.approx(np.array(original["components"]))
    assert altered["training_variance_ratios"] == pytest.approx(original["training_variance_ratios"])
    assert altered["held_out_reconstruction_energy"] != original["held_out_reconstruction_energy"]
    assert original["training_end"] < original["held_out_start"]


def test_historical_replay_excludes_future_observations(market, current):
    frame = market[0]
    row, curve = select_curve(frame, "2024-12-31")
    book = build_book("2024-12-31")
    first = historical_risk(frame, row["Date"], book, curve, window=40)
    changed = frame.with_columns(
        pl.when(pl.col("Date").dt.year() == 2025).then(pl.lit(12345)).otherwise(pl.col("BETA0")).alias("BETA0")
    )
    second = historical_risk(changed, row["Date"], book, curve, window=40)
    assert first == second
    assert first["scenario_end"] == "2024-12-31"


def test_report_headlines_and_all_scenarios_retained(result):
    assert len(result["historical_risk"]["losses"]) == 756
    assert result["historical_risk"]["es_usd"] >= result["historical_risk"]["var_usd"] > 0
    assert result["pca"]["training_end"] == "2024-12-31"
    assert result["pca"]["held_out_end"] == "2025-12-31"
    assert result["checks"]["pricing_engines"]["max_abs_difference_usd"] < 1e-6


def test_embedded_download_bytes_and_no_overwrite(tmp_path):
    packet = build_packet()
    html = packet["index.html"].decode()
    matches = re.findall(r'href="data:[^"]+;base64,([A-Za-z0-9+/=]+)" download="curveshift-([^"]+)"', html)
    names = {
        "analysis.json": "analysis.json",
        "key-rate-risk.csv": "key_rate_risk.csv",
        "historical-losses.csv": "historical_losses.csv",
        "curve-source.csv": "curve_source.csv",
        "source.json": "source.json",
    }
    assert len(matches) == 5
    for encoded, filename in matches:
        assert base64.b64decode(encoded) == packet[names[filename]]
    existing = tmp_path / "report"
    existing.mkdir()
    (existing / "keep.txt").write_text("retain")
    with pytest.raises(ValueError):
        write_report(existing)
    assert (existing / "keep.txt").read_text() == "retain"


def test_scenario_replay_is_source_bound(market, tmp_path):
    inputs = {"as_of": "2025-12-31", "shock_bp": [-25, 0, 25, 0], "source_sha256": market[1]["subset_sha256"]}
    result = evaluate_scenario(inputs)
    assert result["result"]["full_pnl"] < 0
    inputs["source_sha256"] = "wrong"
    with pytest.raises(ValueError, match="source"):
        evaluate_scenario(inputs)
    inputs["source_sha256"] = market[1]["subset_sha256"]
    inputs["shock_bp"] = [True, 0, 0, 0]
    with pytest.raises(ValueError):
        evaluate_scenario(inputs)


def test_api_uses_real_engine_and_rejects_bad_requests(market, current):
    client = TestClient(app)
    assert client.get("/health").json()["observations"] == 2499
    valid = client.post("/scenario", json={"shock_bp": [-25, 0, 25, 0]})
    assert valid.status_code == 200
    assert valid.json()["result"]["full_pnl"] == pytest.approx(scenario(*current, [-25, 0, 25, 0])["full_pnl"])
    for inputs in [
        {"shock_bp": [True, 0, 0, 0]},
        {"shock_bp": [0, 0, 0]},
        {"shock_bp": [301, 0, 0, 0]},
        {"shock_bp": [0, 0, 0, 0], "as_of": "2026-01-01"},
        {"shock_bp": [0, 0, 0, 0], "source_sha256": "different"},
    ]:
        assert client.post("/scenario", json=inputs).status_code == 422


def test_quantlib_calls_restore_global_date(current):
    original = ql.Settings.instance().evaluationDate
    bond, curve = current[0][0], current[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        prices = list(pool.map(lambda _: quantlib_price(bond, "2025-12-31", curve), range(12)))
    assert prices == pytest.approx([prices[0]] * 12)
    assert ql.Settings.instance().evaluationDate == original


def test_full_packet_verifies_and_cli_replays_browser_inputs(tmp_path, market):
    target = tmp_path / "report"
    write_report(target)
    assert verify_report(target)["verified"]
    assert main(["verify", str(target)]) == 0
    inputs = {"as_of": "2025-12-31", "shock_bp": [0, 25, 50, 100], "source_sha256": market[1]["subset_sha256"]}
    input_path = tmp_path / "scenario.json"
    input_path.write_text(json.dumps(inputs))
    result_path = tmp_path / "scenario-result.json"
    assert main(["replay", str(input_path), "--out", str(result_path)]) == 0
    before = result_path.read_bytes()
    assert main(["replay", str(input_path), "--out", str(result_path)]) == 2
    assert result_path.read_bytes() == before


@pytest.mark.parametrize("filename", ["analysis.json", "key_rate_risk.csv", "index.html"])
def test_coherently_rehashed_false_outputs_fail_semantic_replay(tmp_path, filename):
    import hashlib

    target = tmp_path / "report"
    write_report(target)
    path = target / filename
    if filename == "analysis.json":
        data = json.loads(path.read_bytes())
        data["historical_risk"]["var_usd"] += 1000
        path.write_text(json.dumps(data), encoding="utf-8")
    elif filename.endswith(".csv"):
        path.write_bytes(path.read_bytes().replace(b"2Y,", b"fake,", 1))
    else:
        path.write_bytes(path.read_bytes().replace(b"DV01 is a line.", b"False report.", 1))
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_report(target)
