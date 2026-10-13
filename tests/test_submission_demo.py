import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.submission_demo import run, history
from app.nvidia_synthetic import SOURCES
from app.personal_experience_design import compile_context, prepare_local_result

class SubmissionTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'evidence/history-index.json').exists(), 'Requires built public submission evidence projections')
    def test_restart_and_simulated_approval_continue(self):
        with tempfile.TemporaryDirectory() as folder, patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            first=run(folder); second=run(folder)
            self.assertEqual(first['tasks'],second['tasks'])
            self.assertTrue(any(t['state']=='awaiting_approval' for t in first['tasks']))
            done=run(folder,True); restarted=run(folder)
            self.assertTrue(all(t['state']=='completed' for t in done['tasks']))
            self.assertEqual(done['tasks'],restarted['tasks'])
            self.assertEqual(done['metadata'],first['metadata'])
            self.assertEqual(done['new_requests'],0)
            self.assertFalse(done['production_nvidia_worker_connected'])

    def test_metadata_cannot_be_overridden(self):
        context=compile_context(SOURCES)
        self.assertEqual(context['claim_kind'],'personal_report')
        for extra in [{'attribution':None},{'label':'source_fact'},{'answer':'Everyone benefits.'}]:
            with self.assertRaises(ValueError):
                prepare_local_result(json.dumps(dict(assessment='personal_report_only',source_refs=['s2'],**extra)),SOURCES,context)
        with self.assertRaises(ValueError): compile_context(dict(SOURCES,s2=SOURCES['s2']+' changed'))

    @unittest.skipUnless((Path(__file__).resolve().parents[1]/'evidence/history-index.json').exists(), 'Requires built public submission evidence projections')
    def test_recorded_history_integrity_and_no_regrading(self):
        root=Path(__file__).resolve().parents[1]
        records=history(root)
        self.assertEqual(len(records),8)
        self.assertEqual(sum(r['requests'] for r in records),23)
        self.assertEqual(records[-1]['passes'],4)
        self.assertFalse(records[-1]['complete'])
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'evidence'; target.mkdir()
            for source in (root/'evidence').glob('*.json'): (target/source.name).write_bytes(source.read_bytes())
            (target/'recorded-v8.json').write_text('{}')
            with self.assertRaises(ValueError): history(folder)
