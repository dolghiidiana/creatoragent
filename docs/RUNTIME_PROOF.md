# Real worker-runtime proof

The actual CreatorAgent Worker executed exactly one separately approved synthetic NVIDIA Nemotron call through Nebius Token Factory on October 10. The saved payload hash, exact model/endpoint, strict exact-match result, application-owned source metadata, usage and receipt checksum are in evidence/runtime-validation-1.json. It is a safe subset of an earlier actual runtime observation, not a fresh call during playback.

The worker persisted the LIVE_NEBIUS receipt and completed local_review. Reopening the database with transport disabled preserved one dispatch request, completed task states and the same receipt hash. An explicit executor reuse check returned that cached result without transport. Unknown or failed delivery would not retry or fall back.

632 input and 383 completion/reasoning tokens cost $0.00012984 API-derived credits. The cap was $0.001; zero out-of-pocket depended on verified trial/credits and stop-after-trial as well as the local gate. The original response/diagnostics stay private. Official post-V8 totals reconciled; official post-runtime billing remains pending.

This demonstrates a real bounded runtime Token Factory path under the saved official runtime interpretation, not a successful full model audit or general production capability. V1–V8 are preserved and not regraded. Default commands do not enable live transport or spend money.
