"""Submission replay: local fixture and recorded evidence, never live transport."""
import argparse
import hashlib
import json
from html import escape
from pathlib import Path
from .store import Store
from .worker import Worker
from .adapters.fake_destination import FakeDestination
from .creator_context import CreatorContext
from .nvidia_synthetic import SOURCES, context_document
from .personal_experience_design import compile_context, prepare_local_result
from .approvals import packages, decide
from .models import canonical, digest

def history(root):
    folder = Path(root)/'evidence'
    index = json.loads((folder/'history-index.json').read_text(encoding='utf-8'))
    runs = []
    for item in index['runs']:
        path = folder/item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Recorded evidence changed; stop replay')
        runs.append(json.loads(path.read_text(encoding='utf-8')))
    return runs  # Recorded grades only. No historical validator is invoked.

def run(root, simulate_review=False):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    records=history(Path(__file__).resolve().parents[1])
    store=Store(root/'state.sqlite'); destination=FakeDestination(root/'destination.sqlite')
    try:
        store.seed(); memory=CreatorContext(store)
        memory.save('lantern',context_document())
        Worker(store,destination).run()
        if simulate_review:
            for item in packages(store):
                if item['state']=='pending' and item['simulated']:
                    decide(store,item['request_id'],'approved',approver='explicit local simulation')
            Worker(store,destination).run()
        context=compile_context(SOURCES)
        context['permissions']={'local_preparation':True,'cloud_disclosure':False,'public_execution':False}
        # Trust-reviewed metadata belongs to the app. This decision is a local fixture,
        # not a repaired V8 response or a newly validated NVIDIA capability.
        result=prepare_local_result('{"assessment":"personal_report_only","source_refs":["s2"]}',
                                    SOURCES,compile_context(SOURCES))
        bundle={'source_identity':'original synthetic fixture s2','annotation':context,'result':result,
                'decision_origin':'local_fixture_not_model_output'}
        store.db.execute('CREATE TABLE IF NOT EXISTS demo_provenance(id TEXT PRIMARY KEY, body TEXT NOT NULL, checksum TEXT NOT NULL)')
        existing=store.db.execute('SELECT body,checksum FROM demo_provenance WHERE id=?',('personal-report-v1',)).fetchone()
        if existing and (existing['body']!=canonical(bundle) or existing['checksum']!=digest(bundle)):
            raise ValueError('Persisted provenance differs; stop for review')
        if not existing:
            with store.tx():
                store.db.execute('INSERT INTO demo_provenance VALUES(?,?,?)',('personal-report-v1',canonical(bundle),digest(bundle)))
        view={'mode':'local synthetic workflow + read-only historical NVIDIA evidence',
              'live_transport_enabled':False,'new_requests':0,'new_cost_usd':'0',
              'production_nvidia_worker_connected':False,'metadata':bundle,
              'persistent_context':memory.current('lantern')['document'],
              'tasks':[dict(r) for r in store.db.execute('SELECT id,operation,state,attempts FROM tasks ORDER BY id')],
              'review':packages(store),'recorded_runs':records,
              'final_live_request':'unused; scoped stage closed',
              'billing':'Post-V8 officially reconciled. Separate worker-runtime billing pending; this replay makes no request.'}
        (root/'review.json').write_text(json.dumps(view,indent=2),encoding='utf-8')
        sections=[('Persistent workflow',view['tasks']),('Goal, purpose and voice',view['persistent_context']),
                  ('Application-owned provenance — local prototype',bundle),('Consolidated review',view['review'])]
        cards=''.join('<section><h2>'+escape(title)+'</h2><pre>'+escape(json.dumps(body,indent=2))+'</pre></section>' for title,body in sections)
        rows=''.join('<details><summary>'+escape(r['version'])+' — '+str(r['passes'])+'/'+str(r['requests'])+' recorded passes</summary><pre>'+escape(json.dumps(r,indent=2))+'</pre></details>' for r in records)
        html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CreatorAgent submission demo</title><style>body{font:17px system-ui;background:#eff4f2;color:#173c3b;margin:0}main{max-width:940px;margin:auto;padding:24px}section,details{background:white;padding:20px;border-radius:14px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}summary{cursor:pointer}h1{font-size:40px}</style><main><h1>CreatorAgent</h1><p>Remember the purpose. Preserve the voice. Prepare locally. Ask at consequential boundaries.</p><section><h2>Three separate evidence levels</h2><p>Persistent local workflow • recorded NVIDIA audit • locally tested metadata design.</p><p>No live transport. No new spending. No production NVIDIA worker connection.</p><p>V8 passed fact, quotation, unsupported-demand and publication gates, then stopped on personal-experience classification. Voice was not requested. The failed response stays failed.</p></section>'''+cards+'<section><h2>Frozen NVIDIA history on Nebius</h2><p>23 requests; API-derived cumulative credit usage $0.00364890. Final request unused. Post-V8 officially reconciled; separate worker-runtime billing pending. These projections preserve answer content and recorded grades; provider reasoning and identifiers are omitted.</p>'+rows+'</section></main></html>'
        (root/'review.html').write_text(html,encoding='utf-8')
        return view
    finally:
        store.db.close(); destination.db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',default='data/submission-demo')
    parser.add_argument('--simulate-review',action='store_true',help='Approve only the fake destination in this local demonstration')
    args=parser.parse_args(); run(args.root,args.simulate_review)
    print('Local review prepared. No inference or external action performed.')
