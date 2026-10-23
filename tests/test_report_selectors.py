"""Keep precision-report scenario selectors aligned with exact scenario names."""

from html.parser import HTMLParser
import json
import re
import unittest

from stressatlas.demo import demo_inputs
from stressatlas.bundle import artifacts
from stressatlas.precision import precision_artifacts


class PrecisionSelector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.option = None
        self.values = []
        self.scenarios = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "option":
            self.option = (attrs, [])
        elif tag == "tr" and "data-scenario" in attrs:
            self.scenarios.add(attrs["data-scenario"])

    def handle_data(self, data):
        if self.option is not None:
            self.option[1].append(data)

    def handle_endtag(self, tag):
        if tag == "option":
            attrs, text = self.option
            fallback = re.sub(r"[ \t\n\r\f]+", " ", "".join(text)).strip(" \t\n\r\f")
            self.values.append(attrs.get("value", fallback))
            self.option = None


class ReportSelectorTests(unittest.TestCase):
    def test_internal_whitespace_and_markup_names_keep_exact_selector_values(self):
        self.check_labels(["stress  one", "stress one", 'stress\ttwo " & <tail>', "stress\nthree", "baseline"])

    def test_cr_lf_and_nul_names_cannot_collapse_into_other_scenarios(self):
        self.check_labels(["stress\rone", "stress\none", "stress\x00one", "stress�one", "baseline"])

    def check_labels(self, labels):
        data = demo_inputs(30, 1)
        for row, label in zip(data["scenarios"], labels, strict=True):
            row["name"] = label
        data["precision"] = {"resamples": 20}
        html = precision_artifacts(data)["report.html"]
        parser = PrecisionSelector()
        parser.feed(html.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "�"))
        self.assertEqual({json.loads(value) for value in parser.values if value}, set(labels))
        self.assertEqual({json.loads(value) for value in parser.scenarios}, set(labels))
        self.assertEqual(len(parser.values), len(labels) + 1)
        self.assertNotIn("<tail>", html)

    def test_main_report_option_keys_select_the_original_histogram(self):
        data = demo_inputs(30, 1)
        labels = ["stress\rone", "stress\none", "stress\x00one", "stress�one", "baseline"]
        for row, label in zip(data["scenarios"], labels, strict=True):
            row["name"] = label
        html = artifacts(data)["report.html"]
        parser = PrecisionSelector()
        parser.feed(html.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "�"))
        summary = json.loads(html.split("const data=", 1)[1].split(";const select=", 1)[0])
        self.assertEqual({json.loads(value) for value in parser.values}, set(labels))
        self.assertEqual(set(summary["histograms"]), set(labels))


if __name__ == "__main__":
    unittest.main()
