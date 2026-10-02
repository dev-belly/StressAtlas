import contextlib
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import io
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.stats import bootstrap

from stressatlas.bundle import canonical, read_json
from stressatlas.cli import main
from stressatlas.core import Simulation
from stressatlas.demo import demo_inputs
from stressatlas.precision import paired_bootstrap, precision_artifacts, verify_precision_bundle, write_precision_bundle


def simulation(name, losses, fingerprint="shared"):
    values = np.asarray(losses)
    return Simulation(name, values, np.zeros((values.size, 1)), ("test",), 0, 0, {}, fingerprint)


def manual_metrics(values, alpha):
    values = sorted(float(value) for value in values)
    n = len(values)
    probability = Decimal(str(alpha))
    mass = float((1-probability)*n)
    var_index = int((probability*n).to_integral_value(rounding=ROUND_CEILING))-1
    descending = values[::-1]
    full = math.floor(mass)
    total = sum(descending[:full])
    if mass > full:
        total += (mass-full)*descending[full]
    return dict(mean_loss=sum(values)/n, var=values[var_index], es=total/mass)


def linear_quantile(values, probability):
    values = sorted(values)
    position = (len(values)-1)*probability
    low = math.floor(position)
    high = math.ceil(position)
    return values[low]+(position-low)*(values[high]-values[low])


class PrecisionTests(unittest.TestCase):
    def test_baseline_paired_intervals_are_exactly_zero(self):
        summary, samples = paired_bootstrap([simulation("base", [0, 1, 2, 3, 20])], alpha=0.6, resamples=20)
        for row in summary["intervals"]:
            if row["comparison"] == "paired_delta":
                self.assertEqual([row[key] for key in ("estimate", "lower", "upper", "bootstrap_se")], [0, 0, 0, 0])
        self.assertTrue(all(row["es_delta"] == 0 for row in samples))

    def test_translation_preserves_paired_var_and_es_differences(self):
        base = np.array([0, 10, 20, 30, 100], dtype=float)
        summary, samples = paired_bootstrap([simulation("base", base), simulation("shift", base+7)],
                                            alpha=0.6, resamples=40, seed=3)
        for row in summary["intervals"]:
            if row["scenario"] == "shift" and row["comparison"] == "paired_delta":
                self.assertEqual([row[key] for key in ("estimate", "lower", "upper", "bootstrap_se")], [7, 7, 7, 0])
        self.assertTrue(all(row["var_delta"] == 7 and row["es_delta"] == 7
                            for row in samples if row["scenario"] == "shift"))

    def test_same_seed_replays_and_changed_seed_changes_resampling(self):
        cases = [simulation("base", [0, 1, 3, 20, 50])]
        first = paired_bootstrap(cases, alpha=0.6, resamples=20, seed=1)
        self.assertEqual(first, paired_bootstrap(cases, alpha=0.6, resamples=20, seed=1))
        changed = paired_bootstrap(cases, alpha=0.6, resamples=20, seed=2)
        self.assertNotEqual(first[0]["resampling_fingerprint"], changed[0]["resampling_fingerprint"])
        self.assertNotEqual(first[1], changed[1])

    def test_nonbaseline_scenario_order_does_not_change_intervals(self):
        base = np.array([0, 1, 3, 20, 50])
        cases = [simulation("base", base), simulation("double", base*2), simulation("shift", base+2)]
        first, _ = paired_bootstrap(cases, alpha=0.6, resamples=20)
        second, _ = paired_bootstrap([cases[0], cases[2], cases[1]], alpha=0.6, resamples=20)
        key = lambda row: (row["scenario"], row["metric"], row["comparison"])
        self.assertEqual(sorted(first["intervals"], key=key), sorted(second["intervals"], key=key))
        self.assertEqual(first["resampling_fingerprint"], second["resampling_fingerprint"])

    def test_unrelated_driver_banks_cannot_be_paired(self):
        with self.assertRaisesRegex(ValueError, "driver bank"):
            paired_bootstrap([simulation("base", [0, 1, 2]), simulation("other", [0, 1, 2], "other")], resamples=20)

    def test_path_counts_must_match(self):
        with self.assertRaisesRegex(ValueError, "path count"):
            paired_bootstrap([simulation("base", [0, 1, 2]), simulation("other", [0, 1])], resamples=20)

    def test_duplicate_scenario_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            paired_bootstrap([simulation("base", [0, 1]), simulation("base", [0, 1])], resamples=20)

    def test_precision_configuration_is_strict(self):
        cases = [simulation("base", [0, 1, 2])]
        for fields in (dict(resamples=19), dict(resamples=10001), dict(resamples=True),
                       dict(seed=-1), dict(seed=2**32), dict(seed=True),
                       dict(confidence_level=0), dict(confidence_level=1),
                       dict(confidence_level=float("nan")), dict(confidence_level=True)):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                paired_bootstrap(cases, **fields)

    def test_invalid_tail_levels_are_rejected(self):
        for alpha in (0, 1, float("nan"), True):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                paired_bootstrap([simulation("base", [0, 1])], alpha=alpha, resamples=20)

    def test_invalid_loss_samples_are_rejected(self):
        for values in ([0, float("nan")], [0, float("inf")], [0, -1], [False, True]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                paired_bootstrap([simulation("base", values)], resamples=20)

    def test_bootstrap_statistics_match_independent_tail_enumerator(self):
        base = np.array([0, 10, 20, 30, 60], dtype=float)
        summary, samples = paired_bootstrap([simulation("base", base), simulation("double", base*2)],
            alpha=0.5, resamples=40, seed=7, confidence_level=0.8)
        rng = np.random.default_rng(7)
        expected = {name: {metric: [] for metric in ("mean_loss", "var", "es")} for name in ("base", "double")}
        for replicate in range(40):
            indices = rng.integers(0, len(base), size=len(base), dtype=np.int64)
            for name, values in (("base", base), ("double", base*2)):
                result = manual_metrics(values[indices], 0.5)
                row = next(row for row in samples if row["resample"] == replicate and row["scenario"] == name)
                for metric, value in result.items():
                    self.assertAlmostEqual(row[metric], value, places=6)
                    expected[name][metric].append(value)
        for row in summary["intervals"]:
            values = expected[row["scenario"]][row["metric"]]
            if row["comparison"] == "paired_delta":
                values = [value-base_value for value, base_value in zip(values, expected["base"][row["metric"]])]
            self.assertAlmostEqual(row["lower"], linear_quantile(values, 0.1), places=6)
            self.assertAlmostEqual(row["upper"], linear_quantile(values, 0.9), places=6)

    def test_paired_mean_interval_agrees_with_scipy_percentile_bootstrap(self):
        base = np.array([0., 10., 20., 30., 50., 80.])
        current = base*1.5+np.array([1, 2, 3, 4, 5, 6])
        summary, _ = paired_bootstrap([simulation("base", base), simulation("current", current)],
            alpha=0.5, resamples=100, seed=17)
        reference = bootstrap((current, base), lambda a, b: np.mean(a)-np.mean(b),
            n_resamples=100, paired=True, vectorized=False, method="percentile",
            confidence_level=0.95, rng=np.random.default_rng(17))
        row = next(row for row in summary["intervals"] if row["scenario"] == "current"
                   and row["metric"] == "mean_loss" and row["comparison"] == "paired_delta")
        self.assertAlmostEqual(row["lower"], reference.confidence_interval.low, places=6)
        self.assertAlmostEqual(row["upper"], reference.confidence_interval.high, places=6)
        self.assertAlmostEqual(row["bootstrap_se"], reference.standard_error, places=6)

    def test_sparse_tail_and_degenerate_support_are_explicit(self):
        summary, _ = paired_bootstrap([simulation("base", [0]*20)], resamples=20)
        self.assertEqual(len(summary["warnings"]), 3)
        self.assertIn("unseen tail support", summary["warnings"][-1])
        self.assertTrue(all(row["bootstrap_se"] == 0 for row in summary["intervals"]))


class PrecisionBundleTests(unittest.TestCase):
    def data(self):
        return {**demo_inputs(256, 3), "precision": dict(resamples=20, seed=4, confidence_level=0.9)}

    def test_full_simulation_and_bootstrap_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = write_precision_bundle(self.data(), directory)
            result = verify_precision_bundle(directory)
            self.assertEqual((result["replayed_paths"], result["replayed_resamples"]), (256, 20))
            self.assertEqual(summary["interval_rows"], 30)

    def test_rehashed_intervals_resamples_and_summary_cannot_bypass_replay(self):
        for filename in ("intervals.csv", "resamples.csv", "summary.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_precision_bundle(self.data(), root)
                if filename == "summary.json":
                    summary = read_json(root/filename)
                    summary["interval_rows"] += 1
                    content = canonical(summary)
                else:
                    content = (root/filename).read_text().replace("baseline", "fabricated", 1)
                (root/filename).write_text(content)
                manifest = read_json(root/"manifest.json")
                manifest["files"][filename] = sha256((root/filename).read_bytes()).hexdigest()
                (root/"manifest.json").write_text(canonical(manifest))
                with self.assertRaisesRegex(ValueError, "semantic replay"):
                    verify_precision_bundle(root)

    def test_manifest_rejects_extra_artifact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_precision_bundle(self.data(), root)
            manifest = read_json(root/"manifest.json")
            manifest["files"]["../other"] = "0"*64
            (root/"manifest.json").write_text(canonical(manifest))
            with self.assertRaisesRegex(ValueError, "unexpected artifact"):
                verify_precision_bundle(root)

    def test_cli_generation_and_verification(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root/"input.json").write_text(canonical(demo_inputs(128, 2)))
            self.assertEqual(main(["precision", "--inputs", str(root/"input.json"),
                                  "--resamples", "20", "--out", str(root/"out")]), 0)
            self.assertEqual(main(["verify-precision", "--out", str(root/"out")]), 0)

    def test_user_inputs_are_not_labeled_as_synthetic(self):
        data = self.data()
        data.pop("data_kind")
        artifacts = precision_artifacts(data)
        self.assertIn("USER-SUPPLIED PORTFOLIO", artifacts["report.html"])
        self.assertNotIn("SYNTHETIC PORTFOLIO", artifacts["report.html"])

    def test_scenario_labels_are_html_and_spreadsheet_safe(self):
        data = self.data()
        malicious = '=SUM(1)</script><script>alert("x")</script>'
        data["scenarios"][0]["name"] = malicious
        artifacts = precision_artifacts(data)
        self.assertIn("'=SUM", artifacts["intervals.csv"])
        self.assertIn("'=SUM", artifacts["resamples.csv"])
        self.assertNotIn('<script>alert("x")</script>', artifacts["report.html"])
        self.assertIn("&lt;/script&gt;", artifacts["report.html"])

    def test_unknown_precision_setting_is_rejected(self):
        data = self.data()
        data["precision"]["method"] = "invented"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            precision_artifacts(data)
