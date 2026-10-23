import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stressatlas.bundle import csv_text, read_json, verify_bundle, write_bundle
from stressatlas.demo import demo_inputs


class SpreadsheetExportTests(unittest.TestCase):
    def test_formula_strings_and_control_prefixes_are_text(self):
        values = ["=1+1", "+CMD()", "-CMD()", "@SUM(A1)", "\t=1", "\r=1", "\n=1", " =1", "＝1+1", "＋1+1", "－1+1", "＠SUM(1,2)", "\x00=1+1", "\ufeff=1+1"]
        data = csv_text([{"id": value} for value in values], ["id"])
        rows = list(csv.DictReader(io.StringIO(data, newline="")))
        self.assertEqual([row["id"] for row in rows], ["text: " + value for value in values])

    def test_financial_numbers_and_plain_identifiers_stay_unchanged(self):
        values = [-12.5, 0, 4.25, "SME-001", "制造业", None]
        data = csv_text([{"value": value} for value in values], ["value"])
        rows = list(csv.DictReader(io.StringIO(data)))
        self.assertEqual([row["value"] for row in rows], ["-12.5", "0", "4.25", "SME-001", "制造业", ""])

    def test_formula_shaped_header_is_protected(self):
        data = csv_text([{"=formula": 4}], ["=formula"])
        self.assertEqual(list(csv.reader(io.StringIO(data))), [["text: =formula"], ["4"]])

    def test_bundle_keeps_raw_identifier_but_replays_safe_csv(self):
        with TemporaryDirectory() as tmp:
            data = demo_inputs(300, 1)
            data["scenarios"][0]["name"] = "=SCENARIO()"
            write_bundle(data, tmp)
            normalized = read_json(Path(tmp, "inputs.json"))
            self.assertTrue(any(row["name"] == "=SCENARIO()" for row in normalized["scenarios"]))
            self.assertIn("text: =SCENARIO()", Path(tmp, "scenarios.csv").read_text(encoding="utf-8"))
            self.assertTrue(verify_bundle(tmp)["verified"])

    def test_embedded_cr_lf_quotes_and_delimiters_preserve_records(self):
        values = ['name\rnext', 'name\r\nnext', 'name\nnext', 'name, "quoted"']
        text = csv_text([{"id": value, "loss": -12.5} for value in values], ["id", "loss"])
        rows = list(csv.DictReader(io.StringIO(text, newline=""), strict=True))
        self.assertEqual(len(rows), len(values))
        self.assertEqual([row["id"] for row in rows], values)
        self.assertTrue(all(row["loss"] == "-12.5" and len(row) == 2 for row in rows))

    def test_embedded_cr_in_a_header_preserves_the_column(self):
        field = "source\rname"
        text = csv_text([{field: 4}], [field])
        self.assertEqual(list(csv.DictReader(io.StringIO(text, newline=""))), [{field: "4"}])

    def test_distinct_raw_names_remain_distinct_in_csv(self):
        values = ["=1+1", "text: =1+1", "text: plain"]
        text = csv_text([{"id": value} for value in values], ["id"])
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual([row["id"] for row in rows], ["text: " + value for value in values])
