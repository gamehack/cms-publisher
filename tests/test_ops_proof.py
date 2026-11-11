from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import json
import tempfile
import unittest

from cms_publisher import ops_proof

NOW=datetime(2026,11,11,0,30,tzinfo=timezone.utc)
def reports(*rows):
    return {"status":"COMPLETED","reports":list(rows)}
def row(source_key,cms="created",status="COMPLETED"):
    return {"source_key":source_key,"cms":cms,"status":status}

class OpsProofTests(unittest.TestCase):
    def test_complete_profiles_mapped_to_correct_periods(self):
        events=ops_proof.proof_items(reports(
            row("producthunt:2026-11-09"),
            row("x-daily:2026-11-11","already_exists"),
            row("overseas-stories:51","confirmed_after_error"),
            row("trustmrr-weekly:2026-11-09","duplicate"),
        ),NOW)
        self.assertEqual([e["job_id"] for e in events],[
            "ph_cms","x_cms","overseas_cms","trustmrr_cms_weekly"])
        self.assertEqual([e["period_key"] for e in events],[
            "2026-11-09","2026-11-11","2026-11-11","2026-11-09"])
        self.assertTrue(all(e["stage"]=="cms_draft_confirmed" for e in events))
    def test_skipped_no_draft_is_not_reported(self):
        self.assertEqual(ops_proof.proof_items({"status":"SKIPPED","reports":[]},NOW),[])
        self.assertEqual(ops_proof.proof_items(reports(row("x-daily:2026-11-11",
                                                            status="SKIPPED")),NOW),[])
    def test_malformed_or_unconfirmed_receipt_is_rejected(self):
        for key in ("producthunt:2026-02-31","overseas-stories:bad",
                    "not-authorized:2026-11-09"):
            with self.assertRaises(RuntimeError):
                ops_proof.proof_items(reports(row(key)),NOW)
        with self.assertRaises(RuntimeError):
            ops_proof.proof_items(reports(row("producthunt:2026-11-09",cms="UNKNOWN")),NOW)
    def test_monthly_is_separate_unmonitored_stage(self):
        self.assertEqual(ops_proof.proof_items(reports(
            row("trustmrr-monthly:2026-11")),NOW),[])
    @patch.object(ops_proof,"_oidc_token",return_value="a.b.c")
    @patch.object(ops_proof,"lookup_cms_delivery",return_value={"status":"draft","receipt":"id"})
    @patch.object(ops_proof,"urlopen")
    def test_sourcekey_confirmed_before_event_post(self,send,readback,oidc):
        class Resp:
            status=202
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"accepted":true}'
        send.return_value=Resp()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cms.json"
            path.write_text(json.dumps(reports(row("producthunt:2026-11-09"))))
            status=ops_proof.report(path)
        self.assertEqual(status["count"],1)
        readback.assert_called_once()
        request=send.call_args.args[0]
        self.assertEqual(request.full_url,ops_proof.OPS_URL)
        self.assertIn("Bearer a.b.c",request.headers["Authorization"])
    @patch.object(ops_proof,"_oidc_token")
    @patch.object(ops_proof,"lookup_cms_delivery",return_value=None)
    @patch.object(ops_proof,"urlopen")
    def test_missing_cms_row_never_sends_oidc(self,send,lookup,oidc):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"cms.json"
            path.write_text(json.dumps(reports(row("producthunt:2026-11-09"))))
            with self.assertRaisesRegex(RuntimeError,"not confirmed"):
                ops_proof.report(path)
        send.assert_not_called()
        oidc.assert_not_called()

if __name__=="__main__":unittest.main()
