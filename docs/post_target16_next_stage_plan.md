# Post-Target16 Next-Stage Plan

Status date: 2026-05-16
Repo: `C:\Users\13600\Desktop\realtimeASR\111\Agent`
Required test environment: `conda activate agent` through `C:\ProgramData\anaconda3\condabin\conda.bat`.

## Current baseline

Targets 1-20 are complete.

Latest verified commands:

```powershell
cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m py_compile src\orchestrator.py tests\test_agent_safety_convergence.py && python -m pytest tests\test_agent_safety_convergence.py tests\test_safety_policy.py -q"
# 16 passed in 1.81s

cmd /d /c "call C:\ProgramData\anaconda3\condabin\conda.bat activate agent && python -m pytest -q"
# 120 passed in 8.90s
```

## Target 17 - sentence-level safe emotional streaming (complete)

Goal: keep the Target16 safety guarantee while reducing perceived latency for `emotional_agent` output.

Implemented:
- `SystemOrchestrator._run_emotional_agent()` now collects raw stream chunks into a sentence buffer.
- Completed sentences ending in Chinese/ASCII sentence punctuation (`!`, `?`, semicolon, Chinese full-stop/exclamation/question, or newline) are sanitized before SSE `token` emission.
- `crisis` turns remain fully buffered so the crisis safe prefix is applied once and never repeated per sentence.
- Final model output is still sanitized as the authoritative fallback/reconciliation path.

Tests:
- `test_emotional_stream_flushes_completed_safe_sentences`
- `test_emotional_crisis_stream_stays_fully_buffered`
- Existing unsafe stream regression remains active.

## Target 18 - photo semantic metadata and retrieval contract (complete)

Reason: the current album/search path can return photo records, but it cannot reliably know what a photo depicts unless a caption/description/tag has been written into metadata by another component. The next best stage is not to bolt in a heavy vision model blindly; it is to make the storage and retrieval contract explicit and testable.

Scope:
1. Inspect the existing photo interfaces again: `search_family_photos`, photo schema, any `photos` data files, and server routes.
2. Add or normalize photo metadata fields such as `description`, `tags`, `people`, `time_text`, `location`, and `caption_source`.
3. Make retrieval prefer semantic fields when present and gracefully fall back to filename/time/title when absent.
4. Keep future background-agent compatibility: background jobs may enrich captions later, but foreground chat should not block on a lazy captioning call.
5. Add deterministic tests using fixture photo metadata. Do not require network or model downloads.

Non-goals for Target 18:
- Do not pretend image understanding exists without a real caption source.
- Do not introduce network vision inference inside request-time chat flow.
- Do not break existing album search API behavior.



Target 18 completion record:
- `ProfessionalSkills.search_family_photos` now uses existing semantic metadata fields (`description`, `caption`, `tags`, `people`, `location`, `time_text`, `taken_at`, `event`, `album`) for local ranking.
- Non-empty searches merge direct file-service results with an all-record fallback, then rank/filter locally. This makes uploaded descriptions/tags usable without request-time vision inference.
- Photo result payload now preserves the legacy fields (`url`, `desc`, `type`, `tags`) and adds `description`, `people`, `location`, `time_text`, `caption_source`, `original_file_name`, and `metadata_available`.
- Added deterministic tests in `tests/test_photo_keyword_normalization.py` for semantic ranking, all-record fallback, output contract, and non-poisoning imports.
- Verification: focused photo/prompt/safety regression `17 passed in 1.29s`; full regression `115 passed in 11.24s`.

## Target 19 - background-agent action expansion (complete)

After Target18, extend background-agent outputs using explicit action contracts:
- action type
- target channel
- consent/approval requirement
- idempotency key
- visibility boundary: elder/family/community

This should reuse the existing Target12 action-session pattern where possible.

Target 19 completion record:
- `PlannerQueuedAction` now carries explicit contract fields: `target_channel`, `consent_required`, `approval_required`, `visibility_scope`, `idempotency_key`, and `action_session_id`.
- `PlanningAgent` finalizes action contracts deterministically and adds a stable idempotency key.
- `BackgroundPlannerService` persists the action contract into `planner_actions.jsonl` and creates durable `ActionSession` records for frontend-executed scheduled music/story actions.
- `ActionSessionService.create_session()` now reuses an existing session when the same `idempotency_key` is submitted again.
- `SystemOrchestrator` passes the shared `ActionSessionService` into the background planner.
- Verification: focused action/planner regression `12 passed in 2.34s`; full regression after Target19 `118 passed in 8.42s`.

## Target 20 - RAGHelper responsibility migration (complete)

Continue migrating JSON/JSONL persistence away from ad hoc helper writes into typed services/DataStore boundaries. Prioritize areas that background agents and family/community features both touch.


Target 20 completion record:
- `MedicalAgent` can now record symptom reports through `UserContextService` / `ProfileService` / `DataStore` when that service is available, falling back to `RAGHelper` only for legacy isolated construction.
- `SystemOrchestrator` now constructs `UserContextService` before `MedicalAgent` and injects it into `MedicalAgent`.
- `ProfessionalSkills.record_health_complaint` no longer imports or instantiates `RAGHelper`; it writes through `UserContextService` and returns a structured JSON result.
- Added regression coverage for MedicalAgent user-context writes and the health-complaint tool helper.
- Verification: focused Target20 regression `22 passed in 1.43s`; final full regression `120 passed in 8.90s`.

## Post-Target16 plan status

All targets listed in this document are complete through Target20. Before further code work, draft the next planning document instead of appending unrelated scope here.
