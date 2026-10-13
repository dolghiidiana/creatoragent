import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.runtime_nebius import RuntimeNebiusAdapter,_NoRedirect
from app.competition_runtime import payload
from app.nvidia_synthetic import response

class HTTPAdapterTests(unittest.TestCase):
    def test_exact_endpoint_post_no_retry_wire_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.env';key.write_text('NEBIUS_API_KEY=local_test_placeholder')
            wire=json.dumps(response('fact'),indent=2).encode()
            class Reply(io.BytesIO): status=200
            class Opener:
                calls=0
                def open(self,request,timeout):
                    self.calls+=1
                    self.request=request
                    self.timeout=timeout
                    return Reply(wire)
            opener=Opener();adapter=RuntimeNebiusAdapter(enabled=True,key_file=key)
            with patch('urllib.request.build_opener',return_value=opener): actual=adapter(payload())
            self.assertEqual(opener.calls,1);self.assertEqual(opener.timeout,90)
            self.assertEqual(opener.request.full_url,'https://api.tokenfactory.nebius.com/v1/chat/completions')
            self.assertEqual(opener.request.method,'POST');self.assertEqual(json.loads(opener.request.data),payload())
            self.assertEqual(adapter.wire_response,wire.decode());self.assertEqual(actual,response('fact'))
    def test_provider_failure_sanitized_no_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.env';key.write_text('NEBIUS_API_KEY=local_test_placeholder')
            from unittest.mock import Mock
            opener=Mock();opener.open.side_effect=OSError('sensitive body placeholder')
            with patch('urllib.request.build_opener',return_value=opener):
                with self.assertRaises(ValueError) as error: RuntimeNebiusAdapter(enabled=True,key_file=key)(payload())
            self.assertNotIn('sensitive',str(error.exception));self.assertEqual(opener.open.call_count,1)
    def test_redirect_forbidden(self):
        with self.assertRaises(ValueError): _NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid')
