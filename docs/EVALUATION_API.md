# Common evaluation API v1

The API preserves `evaluation.VERSION` and the existing Prism metric denominators. It uses frozen source/gold rows and `execution.capture/restore`, plus a vendored Spectrum runtime adapter. No endpoint applies prompts or edits gold answers. Existing `/eval-*` and comparison history remain readable.

## HTTP contract

- `GET /evaluation-runs` → `{ok:true,items:[RunSummary]}`. Team-scoped and read-only.
- `GET /evaluation-run?id=<string>` → `{ok:true,run:RunDetail}`. Never starts/resumes a model or embedding call.
- `POST /evaluation-start` → accepts `{models:[string],scope:"all"|"eval",baseline_model?:string,limits?:{max_calls?:int},request_id?:string,environment_id?:string}`; returns `{ok:true,id,run,deduplicated:boolean}`. One or multiple models use the same service. Model order is preserved; first is the default baseline. Unsupported limits/environments fail explicitly.
- `POST /evaluation-cancel` → `{id}` → `{ok:true,id,status}`. `cancel_requested` means a call is still in flight; `cancelled` means the worker stopped.
- `POST /evaluation-rerun` → `{id,request_id?}` → `{ok,id,run,deduplicated}`. Creates a fresh run from a terminal source's exact frozen snapshot; no live recapture. The new run records `rerun_of`; duplicate request keys reuse that new run. Running sources and candidate subruns requiring an external guard are rejected.
- `POST /evaluation-resume` → `{id}` → `{ok:true,id,run}`. Uses exactly the stored snapshot; successful items are reused. Completed runs are returned unchanged. No changed inputs accepted.

All mutation routes require the existing admin gate and an explicit team in hosted mode. GET requires login/team through the existing gate. IDs are opaque strings, scoped by the authenticated team; payload team/user fields are rejected; ownership always comes from authentication. Errors use `{ok:false,error,code}`. An idempotency key reused with changed conditions returns `idempotency_conflict`; identical concurrent active starts reuse the same run.

`RunSummary`: `id,kind:"evaluation",status,created_at,updated_at,models:[string],scope,total,completed,failed,pending,cost,can_resume,can_rerun,rerun_of,stalled,basis_fingerprint`.

`RunDetail` additionally includes `snapshot`, `model_results:[{model,total,completed,failed,pending,metrics,evaluation,cost}]`, `items:[{hash,source,expected,got:{[model]:values|null},states:{[model]:status},errors:{[model]:string}}]`, and `comparisons`. Snapshot includes rows, model execution plans, metric version, baseline, scope, limits and dataset fingerprint. Statuses: `queued,running,cancel_requested,cancelled,failed,completed,limit_reached`. Missing cost is `null`: cost `{status:"known"|"partial"|"unknown",known_usd,total_usd,calls,unknown_calls,coverage}`. Unknown call usage never becomes zero; unmetered embeddings are identified explicitly. New contextual winner claims are deferred until a separate validated diagnostic exists.

## Frozen execution API for candidate worker

`prism.evalservice.start_frozen(team, snapshot, *, created_by="", request_id="", background=True, call_guard=None)` returns the same start result. `snapshot` is a deep copy of a saved run's `snapshot`; candidate code may replace cloned `executions[model]` prompt strings, select a fixed development/final subset of `rows`, restrict `models`/`baseline_model`, and pass the remaining call limit. It retains the source metric/service version and environment; the service recomputes the fingerprint. `call_guard(fn,*args,**kwargs)` wraps each actual provider attempt, sharing the candidate budget and cancellation check. No global prompt/module/network patch is allowed. Model/settings/engine validation is enforced on restore.

`prism.evalservice.get_run(run_id, team=None)` returns `{ok,run}`; `list_runs(team=None)`, `cancel(run_id,team=None)`, and `resume(run_id,team=None,*,background=True)` implement the HTTP operations. `start(team=None, models=None, scope="all", baseline_model="", limits=None, request_id="", created_by="", environment_id="", *, background=True)` captures current inputs. `background=False` is for worker tests/trusted candidate code and does not bypass limits or ownership. Returned IDs can be persisted by candidate records. Candidate discovery routes are registered only in `serve.py`.

## Recovery and persistence

Reports use the existing `compare_report` compare-and-swap contract, including atomic index/run creation, so deduplication and ownership do not depend solely on threads. Result and completion state are saved together before moving to the next item. A process lost while a provider call is in flight leaves an uncertain attempt and unknown usage; resume cannot promise provider-side exactly-once billing for that attempt. Completed item results are always reusable. Final metrics and paired comparisons are stored with their version so GET cannot rescore historical results with later code. The service/extractor code fingerprints must still match on resume or rerun. Candidate subruns started with a shared call guard cannot be resumed through an unguarded HTTP request. GET only reports stalled state and never recovers it.

## Read-only preparation and saved environments

- `GET /evaluation-preview?scope=all|eval` returns `{ok,total,available_total,truncated,scope,metric_version,eligibility,prompt:{quality_version,item_version},cost:{status:"unknown",total_usd:null}}`. Counts apply the current scope and 1000-row cap; start captures its own version. No client resolution, writes or model calls.
- `GET /evaluation-environments` returns `{ok,items:[environment]}` for the authenticated team.
- Admin `POST /evaluation-environment-save` accepts `{name,judge_model,max_calls}` and returns `{ok,environment}`. It saves a new immutable environment version; identical saves reuse a version. Saving resolves client settings but performs no model call.
- `POST /evaluation-start` also accepts `environment_id`. The full selected environment is copied to `snapshot.environment`. If `limits` is omitted, the environment's limits become the execution limits; explicit limits take precedence. Environments preserve the current metric definition and provide defaults for separately requested paid diagnostics.

An environment contains `id,name,version,metric_version,diagnostic_version,judge_model,judge_settings,fact_system,coverage_system,max_output_tokens,limits,created_at,created_by`. `judge_settings` is the credential-free `execution.client_settings` contract, with diagnostic retries disabled. Environment selection does not automatically start a diagnostic or modify operational prompts. Saved `judge_model` is the resolved provider model ID, which may include a provider prefix absent from the selection label.

## Current boundaries

The common generation service supports call-count limits and the existing default extraction methodology. Currency budgets, alternate scoring definitions and automatic operational candidate application are not supported and cannot be inferred from completion. Semantic similarity is `not_measured` in the basic run; a separately requested `/eval-grounding-start` diagnostic records embeddings/model usage under its own diagnostic record. Stored partial/unknown costs remain distinct from confirmed zero-cost mock calls. Cross-process deduplication requires the existing atomic `compare_report` and `prism_compare_report_pair` backend contract; it does not use a local filesystem fallback. Hosted RPC integration is covered by contract compatibility, not a live production database test.


## Spectrum PR #1 source verification

User-requested source update: [Spectrum PR #1](https://github.com/onam2518/spectrum-development/pull/1), merged 2026-10-28. Verified head `5c847b39608142a0a7c4160ac6698a0a18c5d94a`, merge `c37c88e3c3a4937040e052886097ff586d95e1ab`, base `4fc290b6dc1261cc1443aa1a077d8cb8b2134f89`. The original local checkout had the PR's new files as working-tree content; its Git HEAD alone did not identify those additions.

The 13 new `spectrum/*.py` files in the authenticated PR patch were reconstructed and compared with the original local files; all were byte-identical. These include `runtime`, `recursive`, `evaluation`, `context_evaluation`, `context_decision`, `autonomy`, `campaign`, `datasets`, `episodes`, `experiment`, `model_worker`, `review_comparison`, and `review_lab`. No upstream delta to the already inspected execution/decision source remained. `spectrum_adapter.py` records the committed PR source rather than only the old local base commit. Every new run stores the Spectrum source revision/hash in `snapshot.engine`.

| Source file | Verified SHA256 | Integration |
| --- | --- | --- |
| `spectrum/runtime.py` | `ea3cf4c287c08740916a6ef69679c33e6679f692ede9734c4423398e4841b85d` | Canonical JSON/hash, secret-field rejection, usage validation, call admission and accounting adapted into `spectrum_adapter.py`; Prism CAS replaces the standalone SQLite runtime. |
| `spectrum/recursive.py` | `46c9f096361a57e1d9b739e63f43be8c1a1132a948ce4d92634bfef6612e7b77` | Candidate worker uses bounded isolated candidate evaluations; operational adoption remains separate. |
| `spectrum/evaluation.py` | `258217b8d434ff8e6211d73e12552497590b30f799d3142499b6bd053315feff` | Reviewed; Prism `evaluation.py` remains the metric/denominator contract. |
| `spectrum/context_evaluation.py` | `68e7fedd7721873baac175f244853021cfbb288f6fe8db6d725f717c4d70d27b` | Reviewed; existing Prism evidence diagnostics remain separately invoked. |
| `spectrum/context_decision.py` | `52a5827a4ef49c43e9c1e6762d18f96c59b8afa3392bd2633342d763a742500e` | Reviewed; the new service does not claim contextual superiority without validated paired judgments. |

The PR's standalone autonomous campaigns, command executors, UI server and experiment adapters are outside the Prism evaluation screen scope. They are not imported into the web process; no external checkout dependency or global module/network patch is introduced.

## Candidate prompt downloads

Admin `GET /prompt-export?candidate=<candidate-id>&round=<positive-integer>` exports a saved candidate from the authenticated team. The ZIP contains the full text of each changed prompt in numbered `prompts/*.txt` files, `changes.diff`, and a `README.txt` identifying the source run, candidate round, goal and separate development/final validation states. It excludes source documents, gold answers and connection settings. A missing candidate, missing source snapshot, invalid round, or unchanged prompt returns an error instead of falling back to the current operational prompt.

Candidate proposals are saved before their evaluation starts. If evaluation stops or exhausts its call limit, already generated prompts remain available and are labeled unverified. Only the selected round can carry the search's final comparison; other rounds retain their own development result and an unverified final status. Downloads perform no model calls or writes and never apply a prompt to operations. The result screen shows the before/after text and offers a separate ZIP download per round.

## Backend verification (2026-10-28)

Final targeted command (all passed, 177 tests):

```sh
python3 -m unittest tests.test_evalservice tests.test_evalcandidate tests.test_evaluation_http tests.test_evalreview tests.test_evalops tests.test_evaluation tests.test_context_eval tests.test_accuracy_eval tests.test_metaeval tests.test_route_dispatch tests.test_get_gate -q
```

`tests/test_evalservice.py` adds 22 temporary-database/mock tests, including snapshot persistence, successful-item reuse after cancellation/failure and a post-save crash, concurrent start deduplication, durable lease ownership, cross-team denial, read-only GET, stored historic scoring, unknown/partial costs, actual-call budgets, external candidate budget guards, source corruption, resolved provider aliases, saved environment isolation, preparation counts and fresh same-condition reruns. The final targeted run completed in 17.756 seconds. A separate route/security run passed 56 tests (`test_route_dispatch`, `test_get_gate`, `test_audit_security`, `test_fail_closed`, `test_authz_cache`). `git diff --check` and Python compilation of the changed backend/test modules passed.

An intermediate combined run exposed mock client-cache leakage from temporary test configurations into legacy resume tests. New fixtures now isolate `serve._LLM_CACHE`; final combined legacy resume tests pass without weakening the production endpoint-change guard. Legacy tests still emit some SQLite/HTTP resource warnings; these are not assertion failures. The new per-call service CAS paths explicitly close their thread-local SQLite connections.

This backend worker made no paid model calls, production database changes, deployment, commit, or edits to the original `/Users/pete.axz-pc/orca/prism` working tree. The isolated worktree contains the implementation. Production Supabase RPC behavior and real-model performance/cost estimates remain unverified; mock success is not evidence of model-quality superiority.

## Integrated verification (2026-10-28)

The final full suite (`python3 -m unittest discover -s tests`) ran 2,384 tests in 56.835 seconds with no failures and 20 conditional skips. JavaScript client checks passed, including the same-condition rerun payload and duplicate-action guard. Candidate comparison tests cover regressions across all measured metrics, including a summary regression hidden by a grade improvement.

Headless Chrome exercised the actual temporary HTTP server with isolated SQLite data and mock model responses: eight cases across two models, result navigation without POST, and a fresh same-condition rerun. Screens at 1440, 768 and 390 pixels had no horizontal overflow; no JavaScript errors were observed. The browser fixture waits for configuration initialization after reviewer onboarding before selecting models. These checks do not establish live-model quality or production database behavior. The temporary server was stopped after verification.
