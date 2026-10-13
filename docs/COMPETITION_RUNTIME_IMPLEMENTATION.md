**Status update October 10:** the actual worker-runtime fact validation passed; post-V8 billing reconciled, post-runtime billing remains pending. See RUNTIME_PROOF.md and READINESS.md for current status. The earlier plan/rule-snapshot text below is retained for context, not current runtime readiness. No full production audit or final submission compliance is claimed.

# Competition runtime integration — locally implemented, live validation pending

The same CreatorAgent Worker now schedules the scoped `synthetic_evidence_audit` → `local_review` graph. It shares Store, CreatorContext, CloudDispatch and the frozen V8 fact validator. A project-scoped worker can coexist with the original launch graph without claiming its tasks. This is a bounded competition path, not a broadly validated production model executor.

## Offline reproduction

```sh
python -m unittest discover -v
python -m app.competition_runtime
python -m app.competition_runtime --mock
python -m app.competition_runtime --mock
```

The default command prepares `data/competition-runtime/runtime-review.html` with transport disabled and consent pending. The explicitly offline mock command uses a separate directory and manufactured response, clearly labeled MOCK. Its second run reuses the persisted receipt without another mock invocation. Neither command has a live-enabled CLI path. Original creator launch reproduction remains in `app.submission_demo`.

## Runtime safety and provenance

The exact request is included in `tests/fixtures/competition-runtime-request.json`; canonical SHA256 `dea1c1da6a992a4dcd318c2af3bde29fc540681393b1d8fea05dba3897d8b889`. Four fictional source statements only. Source identity, speaker, personal-report status, epistemic status, permissions and routing remain application-owned. Model output is checked for consistency; it cannot grant authority to post or change metadata.

One new-stage request, no repairs/retries/fallbacks, 4,000 input tokens and 2,000 completion/reasoning tokens. Rates $0.06/M and $0.24/M imply a $0.00072 conservative reservation inside the $0.001 cap. Zero out-of-pocket requires fresh confirmed provider credit/trial safeguards; a local budget is not a card-charge control. All live checks, including reconciled post-V8 billing, must pass before dispatch. Default transport and the dedicated HTTP adapter are disabled. Credentials are loaded only after durable approved intent; they are never in public state, code, screenshots or logs.

At intent commit, worker ownership/task version/payload/context, exact consent, preflight evidence and reservation are bound atomically. Raw provider responses are immutable local observations; validated raw envelope, strict diagnostic, usage settlement and receipt commit together. Bad model identity/accounting remains uncertain with reserved cost held. A lost response or interrupted pre-commit settlement is blocked and never resent. After successful receipt commit, worker restart reuses it and completes local review. This is at-most-once intent, not exactly-once provider delivery.

## Evidence panel

- **LIVE_NEBIUS:** only an observed result from the dedicated runtime HTTP adapter; client-observed evidence, not cryptographic provider attestation.
- **RECORDED:** unchanged V1–V8 history; not a new runtime request.
- **MOCK:** manufactured offline response; never relabeled as live.

The panel includes validated result, source provenance, exact model/endpoint/payload binding, token usage/cost and restart rule. Raw provider reasoning and identifiers stay in private local evidence. No paid call, public repository publication or competition compliance claim has occurred.

V1–V8 retain their recorded grades and raw private envelopes. V8 passed its four gate cases and failed personal-experience classification; voice was not requested. The old stage is closed with its last unused request untouched.
