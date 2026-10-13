"""Dedicated competition adapter. No retries, redirects, or fallback. Disabled by default."""
import json
import urllib.request
from pathlib import Path
from .models import canonical
from .nebius_contract import ENDPOINT
from .runtime_contract import unique_pairs

class DisabledTransport:
    def __call__(self, request):
        raise ValueError('Runtime transport disabled; no credential lookup or request')

class MockTransport:
    """Test doubles can never produce a LIVE_NEBIUS receipt."""
    def __init__(self, response):
        self.response, self.calls = response, 0
    def __call__(self, request):
        self.calls += 1
        return self.response

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect forbidden')

class RuntimeNebiusAdapter:
    def __init__(self, *, enabled=False, key_file=None):
        self.enabled, self.key_file = enabled, key_file
        self.wire_response = None
    def __call__(self, request):
        if not self.enabled:
            raise ValueError('Runtime transport disabled')
        # Only a designated local secret file, and only AFTER the durable gated intent.
        try:
            lines = Path(self.key_file).read_text(encoding='utf-8-sig').splitlines()
            values = [s.strip().split('=',1)[1].strip().strip('\"\'') for s in lines
                      if s.strip().startswith('NEBIUS_API_KEY=')]
            if len(values)!=1 or not values[0] or any(c.isspace() for c in values[0]):
                raise ValueError()
            req = urllib.request.Request(ENDPOINT+'chat/completions',
                data=canonical(request).encode('utf-8'),method='POST',
                headers={'Authorization':'Bearer '+values[0],'Content-Type':'application/json'})
            with urllib.request.build_opener(_NoRedirect()).open(req,timeout=90) as response:
                if response.status != 200: raise ValueError()
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise ValueError()
                decoded=json.loads(raw,object_pairs_hook=unique_pairs)
                self.wire_response=raw.decode('utf-8')
                return decoded
        except Exception:
            raise ValueError('Nebius delivery or response uncertain; no retry or fallback') from None
