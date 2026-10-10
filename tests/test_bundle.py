import contextlib
from hashlib import sha256
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stressatlas.bundle import canonical, read_json, verify_bundle, write_bundle
from stressatlas.cli import main
from stressatlas.demo import demo_inputs


class BundleTests(unittest.TestCase):
    def test_cli_preserves_pd_endpoints_with_underflowed_odds(self):
        data={"portfolio":[
            dict(loan_id="L0",obligor_id="B0",sector="retail",ead=1000,pd=0,lgd=.5,rho=.25),
            dict(loan_id="L1",obligor_id="B1",sector="retail",ead=1000,pd=1,lgd=.5,rho=.25),
        ],"scenarios":[dict(name="tiny-odds",pd_odds_multiplier=5e-324,
                            sector_shocks=[dict(sector="retail",pd_odds_multiplier=5e-324)])],
            "config":dict(paths=100,seed=1,alpha=.99,global_share=.5)}
        with TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            inputs=Path(tmp,"inputs.json");out=Path(tmp,"report")
            inputs.write_text(canonical(data),encoding="utf-8",newline="")
            self.assertEqual(main(["run","--inputs",str(inputs),"--out",str(out)]),0)
            row=read_json(out/"summary.json")["scenarios"][0]
            self.assertEqual([row[key] for key in ("analytic_el","mean_loss","var","es")],[500]*4)
            self.assertEqual(main(["verify","--out",str(out)]),0)

    def test_cli_rejects_enormous_integer_ead_cleanly(self):
        data=demo_inputs(100,1);data["portfolio"][0]["ead"]=10**400
        with TemporaryDirectory() as tmp:
            inputs=Path(tmp,"inputs.json");out=Path(tmp,"report")
            inputs.write_text(canonical(data),encoding="utf-8",newline="")
            stderr=io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["run","--inputs",str(inputs),"--out",str(out)]),2)
            self.assertIn("invalid ead",stderr.getvalue())
            self.assertNotIn("Traceback",stderr.getvalue())
            self.assertFalse(out.exists())

    def test_bundle_replays_from_normalized_saved_inputs(self):
        with TemporaryDirectory() as tmp:
            write_bundle(demo_inputs(300,1),tmp)
            self.assertTrue(verify_bundle(tmp)["verified"])

    def test_deterministic_evidence(self):
        with TemporaryDirectory() as a,TemporaryDirectory() as b:
            write_bundle(demo_inputs(300,1),a);write_bundle(demo_inputs(300,1),b)
            self.assertEqual({p.name:p.read_bytes() for p in Path(a).iterdir()},{p.name:p.read_bytes() for p in Path(b).iterdir()})

    def test_modified_artifact_is_rejected(self):
        with TemporaryDirectory() as tmp:
            write_bundle(demo_inputs(300,1),tmp)
            Path(tmp,"scenarios.csv").write_text("modified",encoding="utf-8",newline="")
            with self.assertRaisesRegex(ValueError,"hash mismatch"):
                verify_bundle(tmp)

    def test_rehashed_false_es_is_rejected_by_semantic_replay(self):
        with TemporaryDirectory() as tmp:
            write_bundle(demo_inputs(300,1),tmp)
            path=Path(tmp,"summary.json");summary=read_json(path);summary["scenarios"][0]["es"]=0
            path.write_text(canonical(summary),encoding="utf-8",newline="")
            manifest=read_json(Path(tmp,"manifest.json"));manifest["files"][path.name]=sha256(path.read_bytes()).hexdigest()
            Path(tmp,"manifest.json").write_text(canonical(manifest),encoding="utf-8",newline="")
            with self.assertRaisesRegex(ValueError,"semantic replay mismatch"):
                verify_bundle(tmp)

    def test_report_and_json_escape_scenario_identifiers(self):
        with TemporaryDirectory() as tmp:
            data=demo_inputs(300,1);data["scenarios"][0]["name"]="</script><script>unsafe</script>"
            write_bundle(data,tmp)
            html=Path(tmp,"report.html").read_text(encoding="utf-8")
            self.assertNotIn("</script><script>unsafe</script>",html)
            self.assertIn("&lt;/script&gt;",html)
            self.assertTrue(verify_bundle(tmp)["verified"])

    def test_sample_is_labeled_and_scenario_path_does_not_overwrite_index(self):
        with TemporaryDirectory() as tmp:
            data=demo_inputs(300,1);data["scenarios"][0]["name"]="path"
            write_bundle(data,tmp)
            header=Path(tmp,"loss_sample.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(header.startswith("path,loss:path,"))
            self.assertEqual(len(Path(tmp,"loss_sample.csv").read_text(encoding="utf-8").splitlines()),201)

    def test_small_tail_warns_in_report(self):
        with TemporaryDirectory() as tmp:
            summary=write_bundle(demo_inputs(300,1),tmp)
            self.assertTrue(summary["warnings"])
            self.assertIn("Fewer than 100",Path(tmp,"report.html").read_text(encoding="utf-8"))

    def test_custom_inputs_are_not_described_as_synthetic(self):
        with TemporaryDirectory() as tmp:
            data=demo_inputs(300,1);data.pop("data_kind")
            write_bundle(data,tmp)
            html=Path(tmp,"report.html").read_text(encoding="utf-8")
            self.assertIn("USER-SUPPLIED PORTFOLIO",html)
            self.assertNotIn("Synthetic demo",html)

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        with TemporaryDirectory() as tmp:
            for text in ('{"a":NaN}','{"a":1,"a":2}'):
                Path(tmp,"bad.json").write_text(text,encoding="utf-8",newline="")
                with self.assertRaises(ValueError):
                    read_json(Path(tmp,"bad.json"))

    def test_unexpected_manifest_path_is_rejected(self):
        with TemporaryDirectory() as tmp:
            write_bundle(demo_inputs(300,1),tmp)
            manifest=read_json(Path(tmp,"manifest.json"));manifest["files"]["../outside"]="0"
            Path(tmp,"manifest.json").write_text(canonical(manifest),encoding="utf-8",newline="")
            with self.assertRaisesRegex(ValueError,"unexpected artifact"):
                verify_bundle(tmp)

    def test_malformed_manifest_has_a_clear_validation_error(self):
        with TemporaryDirectory() as tmp:
            for payload in ([], {"files":None}, {"files":[]}):
                Path(tmp,"manifest.json").write_text(canonical(payload),encoding="utf-8",newline="")
                with self.assertRaisesRegex(ValueError,"unsupported manifest"):
                    verify_bundle(tmp)
