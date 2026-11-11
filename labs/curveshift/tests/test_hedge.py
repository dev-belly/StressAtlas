"""Independent LP optima, sealed evaluation data and evidence replay contracts."""

import base64
import hashlib
import json
import re

import numpy as np
import polars as pl
import pytest
from fastapi.testclient import TestClient

from curveshift.api import app
from curveshift.cli import main
from curveshift.engine import BondSpec, build_book, quantlib_price
from curveshift.hedge import evaluate_hedge, hedge_study, prepare_hedge_data, scenario_losses, solve_cvar
from curveshift.hedge_report import hedge_packet, verify_hedge_report, write_hedge_report
from curveshift.market import curve_from_row, load_market, select_curve
from curveshift.research import tail_risk


@pytest.fixture(scope="module")
def market():
    return load_market()[0]


@pytest.fixture(scope="module")
def study():
    return hedge_study()


def test_one_instrument_has_a_hand_computable_optimum():
    # One short $1m removes identical exposures. More shorting loses on the opposite scenario.
    target = np.array([-100, -20, 20, 100.0])
    result = solve_cvar(target, target[:, None], 2_000_000, cost_bp=0.1, confidence=0.5)
    assert result["positions_face_usd"] == pytest.approx([-1_000_000])
    assert result["training_risk"]["es_usd"] == pytest.approx(0, abs=1e-8)
    assert result["entry_cost_usd"] == pytest.approx(10)
    assert result["objective_usd"] == pytest.approx(10)
    assert result["solver"]["primal_dual_gap_usd"] < 1e-6


def test_fractional_tail_and_binding_budget_have_known_solution():
    target = np.array([-100, -20, 20, 100.0])
    result = solve_cvar(target, target[:, None], 250_000, cost_bp=0.1, confidence=0.625)
    assert result["positions_face_usd"] == pytest.approx([-250_000])
    # Tail mass is 1.5 observations: (75 + .5*15) / 1.5 = 55.
    assert result["training_risk"]["es_usd"] == pytest.approx(55)
    assert result["objective_usd"] == pytest.approx(57.5)


def test_high_entry_cost_can_make_zero_trade_optimal():
    target = np.array([-100, -20, 20, 100.0])
    result = solve_cvar(target, target[:, None], 2_000_000, cost_bp=50, confidence=0.5)
    assert result["gross_face_usd"] == pytest.approx(0)
    assert result["objective_usd"] == pytest.approx(60)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"budget_usd": -1},
        {"budget_usd": True},
        {"budget_usd": float("nan")},
        {"budget_usd": 10_000_001},
        {"budget_usd": 1, "cost_bp": -1},
        {"budget_usd": 1, "cost_bp": float("inf")},
        {"budget_usd": 1, "confidence": 1},
    ],
)
def test_invalid_constraints_fail(kwargs):
    with pytest.raises(ValueError):
        solve_cvar([1, 2], [[1], [2]], **kwargs)


def test_misaligned_or_nonfinite_scenarios_fail():
    for target, hedge in [
        ([1, 2], [[1]]),
        ([1, 2], [[1], [float("nan")]]),
        ([1, 2], [1, 2]),
        ([1], [[1]]),
        ([1, 2], [[], []]),
    ]:
        with pytest.raises(ValueError):
            solve_cvar(target, hedge, 1_000_000)


def test_heldout_curve_mutation_cannot_change_the_fitted_trades(market):
    original = prepare_hedge_data(market)
    changed = market.with_columns(
        pl.when(pl.col("Date").dt.year() == 2025).then(pl.col("BETA0") + 0.5).otherwise(pl.col("BETA0")).alias("BETA0")
    )
    altered = prepare_hedge_data(changed)
    a, b = evaluate_hedge(original), evaluate_hedge(altered)
    assert original["training"] == pytest.approx(altered["training"])
    assert a["positions_face_usd"] == pytest.approx(b["positions_face_usd"])
    assert a["training_risk"] == b["training_risk"]
    assert a["evaluation_risk"]["es_usd"] != pytest.approx(b["evaluation_risk"]["es_usd"])


def test_full_revaluation_columns_match_independent_quantlib(market):
    row, curve = select_curve(market, "2024-12-31")
    rows = list(market.tail(2).iter_rows(named=True))
    bond = build_book("2024-12-31", [BondSpec("check", 7, 0.035, 1_000_000)])[0]
    previous, following = (curve_from_row(r) for r in rows)

    def shifted(t):
        return curve(t.copy()) + following(t.copy()) - previous(t.copy())

    expected = quantlib_price(bond, row["Date"], curve) - quantlib_price(bond, row["Date"], shifted)
    assert scenario_losses(rows, [bond], curve)[0, 0] == pytest.approx(expected, abs=1e-6)


def test_all_budgets_respect_caps_and_objectives(study):
    assert study["training_end"] < study["evaluation_start"]
    assert study["training_changes"] == 504 and study["evaluation_changes"] == 249
    previous = float("inf")
    for case in study["cases"]:
        positions = np.array(case["positions_face_usd"])
        assert max(abs(positions)) <= 3_000_000 + 1e-5
        assert abs(positions).sum() <= case["budget_usd"] + 1e-5
        assert case["gross_face_usd"] == pytest.approx(abs(positions).sum())
        assert case["entry_cost_usd"] == pytest.approx(abs(positions).sum() * 2 / 10000)
        assert case["objective_usd"] <= previous + 1e-5
        previous = case["objective_usd"]
        losses = [row["hedged_loss_usd"] for row in case["evaluation_losses"]]
        assert case["evaluation_risk"] == tail_risk(losses, study["confidence"])
        assert case["evaluation_es_plus_entry_usd"] == pytest.approx(
            case["evaluation_risk"]["es_usd"] + case["entry_cost_usd"]
        )


def test_api_matches_solver_and_rejects_invalid_requests(study):
    client = TestClient(app)
    response = client.post("/hedge", json={"budget_usd": 6_000_000, "cost_bp": 2})
    assert response.status_code == 200
    assert response.json()["cases"][0]["positions_face_usd"] == pytest.approx(study["cases"][3]["positions_face_usd"])
    for bad in [
        {"budget_usd": True},
        {"budget_usd": "6000000"},
        {"budget_usd": -1},
        {"budget_usd": 10_000_001},
        {"cost_bp": 51},
        {"confidence": 0.9},
        {"source_sha256": "wrong"},
    ]:
        assert client.post("/hedge", json=bad).status_code == 422


def test_cli_and_report_replay_preserve_outputs(tmp_path, capsys):
    assert main(["hedge", "--budget-usd", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"][0]["gross_face_usd"] == 0
    target = tmp_path / "hedge"
    assert main(["hedge-report", "--out", str(target)]) == 0
    assert main(["hedge-verify", str(target)]) == 0
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(ValueError):
        write_hedge_report(target)
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


def test_downloads_preserve_exact_packet_bytes(study):
    packet = hedge_packet(study)
    matches = re.findall(
        r'href="data:[^"]+;base64,([A-Za-z0-9+/=]+)" download="curveshift-([^"]+)"', packet["index.html"].decode()
    )
    names = {"hedge.json": "hedge.json", "frontier.csv": "frontier.csv", "heldout-losses.csv": "heldout_losses.csv"}
    assert len(matches) == 3
    for encoded, filename in matches:
        assert base64.b64decode(encoded) == packet[names[filename]]


@pytest.mark.parametrize("filename", ["hedge.json", "frontier.csv", "index.html"])
def test_rehashed_false_results_still_fail_replay(tmp_path, filename):
    target = tmp_path / "hedge"
    write_hedge_report(target)
    path = target / filename
    if filename == "hedge.json":
        value = json.loads(path.read_bytes())
        value["cases"][3]["positions_face_usd"][0] += 100_000
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path.write_bytes(b"false\n" + path.read_bytes())
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_hedge_report(target)
