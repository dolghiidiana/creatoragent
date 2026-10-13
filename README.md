# CreatorAgent

Remember the purpose. Preserve the voice. Prepare the work. Ask at consequential boundaries.

CreatorAgent is a persistent local creator-workflow prototype. It stores purpose, priorities, identity and voice alongside tasks, sources, approvals and artifacts. Routine preparation continues; public actions and new cloud disclosure need scoped consent.

## Try it locally — no account, key or payment required

This release is a **Python source test build**, not a hosted app or Windows executable. Install Python 3.11+, extract the ZIP into a writable folder, and open a terminal in the folder containing README.md and app/. No pip install is needed: standard library only. On Windows, use `py -3` instead of `python` if that is your installed launcher. Generated state is written beneath data/; keep it out of the public release.

From that extracted repository root:

```sh
python -m unittest discover -v
python -m app.submission_demo
python -m app.submission_demo
python -m app.submission_demo --simulate-review
python -m app.submission_demo
python -m app.competition_runtime
python -m app.competition_runtime --mock
python -m app.competition_runtime --mock
```

Open `data/submission-demo/review.html` in your browser (on Windows: `Start-Process .\data\submission-demo\review.html`). Open `data/competition-runtime/runtime-review.html` and `data/competition-runtime-mock/runtime-review.html` for the disabled and MOCK runtime modes, respectively. These are the default output paths; a custom --root changes the output folder. Repeating the workflow preserves completed work. `--simulate-review` approves only a fake destination and demonstrates permitted local continuation; nothing is posted. The runtime default is disabled and awaiting consent. Explicit MOCK uses separate state and manufactured responses; its receipt is not a provider call. These commands cannot enable live inference.

The original fictional Lantern Notebook fixture demonstrates a deterministic subset of the creator-launch workflow. It is not a whole-book analysis or a live generative marketing package. No published-book passages, unpublished material, credentials or state databases belong in this release.

## Personal Compass

Personal Compass is the creator-facing explanation of the durable `CreatorContext` layer: goal, purpose, priorities, identity, voice, source scope and boundaries. It is stored project context, not an always-on companion or automatic knowledge of someone's life. Context hashes bind approved runtime work; changed context blocks dispatch. Routine steps remain grouped into one review rather than repeated micro-approvals.

## Real Nebius / NVIDIA runtime proof

On October 10, 2026, the actual CreatorAgent Worker invoked `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` through `https://api.tokenfactory.nebius.com/v1/`. Exactly one consent/budget-gated synthetic fact request returned `twelve` and passed the strict contract. Usage: 632 input and 383 completion/reasoning tokens; API-derived cost $0.00012984, below the $0.001 cap; zero out-of-pocket. The worker persisted a LIVE_NEBIUS receipt and completed local review. Database reopen with transport disabled reused the same receipt; no second request occurred.

See [safe receipt projection](evidence/runtime-validation-1.json) and [runtime proof](docs/RUNTIME_PROOF.md). This is evidence of an actual earlier runtime call, not a new call during replay. Provider reasoning/response identifiers remain outside the release. Token Factory availability was checked with an authenticated read-only model lookup, separately from the one inference.

Post-V8 official billing reconciled exactly. **Official billing for the new runtime request remains pending**; API-derived usage is not presented as final account reconciliation. Transport is disabled again. This narrow fact audit does not establish general model reliability or a fully validated production NVIDIA workflow.

## Honest evaluation history

V1–V8 retain their original recorded outcomes. V8 passed four gates—fact, quotation, unsupported demand and publication boundary—then failed personal-experience label/attribution; voice was not requested. The closed stage used 23 requests / $0.00364890 credits and left its final request unused. The separately approved runtime stage used one request / $0.00012984. Replay checks historical hashes without regrading.

That finding informed application-owned, reviewed source identity, speaker, personal-report status, epistemic status and permissions. The model checks bounded evidence; it does not own consent or transform subjective experience into established fact. Arbitrary manuscript classification remains outside this prototype.

## Boundaries and implementation

SQLite persists tasks, context, approvals, events, intent and receipts. Exact payload/context consent and conservative cost reservation are committed before transport. Unknown delivery stops with its reservation held; no retry or fallback. A successful receipt is reused after restart. This is at-most-once intent, not a guarantee of exactly-once provider delivery. Client-observed receipts are not cryptographic provider attestations.

Live use requires an operator-reviewed exact payload, fresh authenticated model/privacy/rate/token/billing checks, confirmed trial/credit protection, and a separate spending authorization. A local cap is not a provider card-charge setting. Do not enable paid transport to rehearse this demo. No live-enabled CLI, hosted service or real public-posting adapter is provided.

## Review and release status

[Architecture](docs/ARCHITECTURE.md) · [recording script](docs/RECORDING.md) · [Devpost draft](docs/SUBMISSION_DRAFT.md) · [readiness checklist](docs/READINESS.md) · [rule snapshot](docs/RULES_AND_GAPS.md) · [third-party notices](docs/THIRD_PARTY_NOTICES.md).

The approved narrated/captioned demo is complete (2:32) and available at https://www.youtube.com/watch?v=vMibr82xocU. Diana reports the Devpost media section is complete. Repository publication, public Python source test-build access and final Devpost submission remain pending approval. Rule snapshot was checked October 9/10; recheck official rules before submission. The recorded deadline is October 30, 2026, 13:00 Toronto EDT.

Software: MIT; see LICENSE. No model weights are redistributed. NVIDIA model and Nebius service terms are separate. No sponsor endorsement or sales-demand claim is made.


