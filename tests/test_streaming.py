"""Execution modes must agree on paths and all published financial evidence."""

import contextlib
import io
import tracemalloc
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from stressatlas.bundle import artifacts, decode, read_json, verify_bundle
from stressatlas.cli import main
from stressatlas.core import (
    Loan,
    Scenario,
    make_drivers,
    paired_delta,
    simulate,
    tail_metrics,
)
from stressatlas.demo import demo_inputs
from stressatlas.precision import precision_artifacts, verify_precision_bundle


class StreamingTests(unittest.TestCase):
    def test_driver_bytes_match_original_full_array_draws(self):
        loans, _, _ = decode(demo_inputs(4201, 42))
        for seed in (0, 42, 2**32 - 1):
            with self.subTest(seed=seed):
                bank = make_drivers(list(reversed(loans)), 4201, seed, mode="stream")
                streams = [
                    np.random.default_rng(s)
                    for s in np.random.SeedSequence(seed).spawn(3)
                ]
                expected = (
                    streams[0].standard_normal(4201),
                    streams[1].standard_normal((4201, len(bank.sectors))),
                    streams[2].standard_normal((4201, len(bank.obligors))),
                )
                digest = sha256(
                    repr((bank.obligors, bank.sectors, 4201, seed, "PCG64")).encode()
                )
                for array in expected:
                    digest.update(array.astype("<f8", copy=False).tobytes())
                self.assertEqual(bank.fingerprint, digest.hexdigest())
                for chunk in (7, 4096, 5000):
                    blocks = list(bank.blocks(chunk))
                    self.assertEqual(
                        [row[0] for row in blocks], list(range(0, 4201, chunk))
                    )
                    for column, array in enumerate(expected, 1):
                        np.testing.assert_array_equal(
                            np.concatenate([row[column] for row in blocks]), array
                        )

    def test_every_scenario_and_chunk_preserves_financial_paths(self):
        loans, scenarios, _ = decode(demo_inputs(503, 7))
        memory = make_drivers(loans, 503, 7)
        stream = make_drivers(loans, 503, 7, mode="stream")
        baseline = simulate(loans, scenarios[0], memory)
        for scenario in scenarios:
            expected = simulate(loans, scenario, memory)
            for chunk in (1, 7, 251, 502, 4096):
                with self.subTest(scenario=scenario.name, chunk=chunk):
                    actual = simulate(loans, scenario, stream, chunk_size=chunk)
                    np.testing.assert_array_equal(actual.losses, expected.losses)
                    np.testing.assert_array_equal(
                        actual.sector_losses, expected.sector_losses
                    )
                    self.assertEqual(actual.default_rates, expected.default_rates)
                    self.assertEqual(actual.analytic_el, expected.analytic_el)
                    self.assertEqual(actual.stressed_ead, expected.stressed_ead)
                    self.assertEqual(
                        tail_metrics(actual.losses), tail_metrics(expected.losses)
                    )
                    self.assertEqual(
                        paired_delta(actual, baseline), paired_delta(expected, baseline)
                    )

    def test_stream_reuse_preserves_shared_borrowers_and_pd_endpoints(self):
        loans = [
            Loan("L0", "B0", "s", 1000, 0, 0.5, 1),
            Loan("L1", "B1", "s", 1000, 1, 0.5, 1),
        ]
        split = [
            loans[0],
            replace(loans[1], ead=400),
            replace(loans[1], loan_id="L2", ead=600),
        ]
        bank = make_drivers(loans, 101, 1, mode="stream")
        for portfolio in (loans, split, list(reversed(split))):
            result = simulate(portfolio, Scenario("base"), bank, chunk_size=3)
            np.testing.assert_array_equal(result.losses, np.full(101, 500.0))
            self.assertEqual(result.default_rates, {"B0": 0.0, "B1": 1.0})

    def test_complete_scenario_and_precision_artifacts_are_byte_identical(self):
        data = demo_inputs(503, 42)
        self.assertEqual(artifacts(data), artifacts(data, driver_mode="stream"))
        data["precision"] = dict(resamples=20, seed=7, confidence_level=0.95)
        self.assertEqual(
            precision_artifacts(data), precision_artifacts(data, driver_mode="stream")
        )

    def test_all_cli_routes_cross_verify_modes_without_recording_execution_choice(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            memory, stream, custom = [
                Path(tmp, name) for name in ("memory", "stream", "custom")
            ]
            self.assertEqual(
                main(["demo", "--paths", "101", "--seed", "7", "--out", str(memory)]), 0
            )
            self.assertEqual(
                main(
                    [
                        "demo",
                        "--paths",
                        "101",
                        "--seed",
                        "7",
                        "--out",
                        str(stream),
                        "--driver-mode",
                        "stream",
                    ]
                ),
                0,
            )
            self.assertEqual(
                {p.name: p.read_bytes() for p in memory.iterdir()},
                {p.name: p.read_bytes() for p in stream.iterdir()},
            )
            self.assertEqual(
                main(
                    [
                        "run",
                        "--inputs",
                        str(memory / "inputs.json"),
                        "--out",
                        str(custom),
                        "--driver-mode",
                        "stream",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(["verify", "--out", str(memory), "--driver-mode", "stream"]), 0
            )
            self.assertTrue(verify_bundle(stream)["verified"])
            self.assertEqual(
                read_json(custom / "summary.json"), read_json(memory / "summary.json")
            )
            self.assertEqual(
                main(
                    [
                        "precision",
                        "--inputs",
                        str(memory / "inputs.json"),
                        "--resamples",
                        "20",
                        "--out",
                        str(stream / "precision"),
                        "--driver-mode",
                        "stream",
                    ]
                ),
                0,
            )
            self.assertTrue(verify_precision_bundle(stream / "precision")["verified"])
            self.assertEqual(
                main(
                    [
                        "verify-precision",
                        "--out",
                        str(stream / "precision"),
                        "--driver-mode",
                        "stream",
                    ]
                ),
                0,
            )
            self.assertNotIn("driver_mode", read_json(stream / "inputs.json")["config"])

    def test_driver_generation_does_not_allocate_a_path_by_obligor_bank(self):
        # A retained 80,000 x 64 float64 bank alone would require 39 MiB.
        # Measure actual NumPy allocations, rather than the shape of helper calls.
        loans = [Loan(f"L{i}", f"B{i}", "s", 1000, 0.2, 0.5, 0.25) for i in range(64)]
        tracemalloc.start()
        try:
            bank = make_drivers(loans, 80000, 7, mode="stream")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(bank.paths, 80000)
        self.assertLess(peak, 12 * 1024**2)

    def test_invalid_execution_modes_fail_before_allocating(self):
        loans, _, _ = decode(demo_inputs(101, 7))
        for mode in ("unknown", "", None, True):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(ValueError, "driver mode"),
            ):
                make_drivers(loans, 101, 7, mode=mode)
        with TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            out = Path(tmp, "output")
            with self.assertRaises(SystemExit) as error:
                main(["demo", "--driver-mode", "unknown", "--out", str(out)])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(out.exists())
