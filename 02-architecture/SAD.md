# Software Architecture Document (SAD) — taskq-api

## 1. Architecture Overview

`taskq-api` is an ASGI HTTP service (FastAPI, `uvicorn taskq_api.app:app`) that
turns the Round-1 CLI task queue into a REST API with relational persistence.
Requests enter through a single FastAPI app, pass a shared auth+scope+rate-limit
dependency, are handled by thin API handlers that delegate to a `service/`
business layer, which reads/writes through a `repository/` layer holding all
`Session`/transaction boundaries over SQLAlchemy 2.x ORM models. Schema evolves
via three Alembic revisions (v1→v2→v3). Task execution runs out-of-band via an
`asyncio.TaskGroup` background executor spawning `asyncio.create_subprocess_exec`
subprocesses (never `shell=True`). All non-2xx responses are RFC 7807
`application/problem+json`. A `python -m taskq_api` entrypoint provides
`migrate` / `seed` / `healthcheck` / `key create` management commands.

Layering is a hard contract (SPEC.md NFR-06, `.importlinter`):

```
api  >  service  >  repository  >  models
```

Upper layers may import lower layers; lower layers MUST NOT import upper
layers. `config` and `exceptions`/`redaction` are independent (importable by
any layer, import nothing project-internal upward). Only `repository/` and
`models/` may import `sqlalchemy` — this is enforced as a forbidden-import
contract, not just a convention, because ORM leaking into `service`/`api` is
the concrete anti-pattern this round is designed to catch.

### 1.1 Technology Stack (SPEC.md §2)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| HTTP framework | FastAPI (ASGI) | async-native, dependency-injection auth/scope/rate-limit, auto OpenAPI (NFR-05) |
| Validation | pydantic v2 | request/response schema validation (FR-01) |
| ORM | SQLAlchemy 2.x, declarative + explicit `Session` | isolates transaction boundaries in `repository/` (FR-06) |
| Database | SQLite (dev/test), PostgreSQL (prod) — one ORM model set | portability without dialect-specific ORM code |
| Migration | Alembic, 3 revisions each with `downgrade` | reversible schema evolution incl. data migration (FR-07) |
| Async | `async def` endpoints + `asyncio.TaskGroup` | background task execution, graceful drain (FR-08) |
| Auth | `X-API-Key` header, SHA-256 hash + `hmac.compare_digest` | no plaintext keys at rest, constant-time compare (FR-03) |
| Rate limiting | per-token token bucket, DB-backed row lock | consistent across workers (FR-05) |
| Error contract | RFC 7807 `application/problem+json` | uniform, detail-scrubbed error body (FR-10) |
| Layering | `import-linter` | machine-checked `.importlinter` contract (NFR-06) |

### 1.2 System Verification Target
> **Every exit gate (2, 3 and 4)**: the harness executes `make verify-system`. A
> non-zero exit fails the gate. The target name is fixed — the harness always calls
> `make verify-system`.
>
> This is the only check in the whole framework that runs the delivered system.
> Everything else reads your source text or runs your test suite, both of which
> your test doubles configure. Two rules follow, and the gate enforces both:
>
> 1. **At least one step must invoke the delivered entry point** — the program a
>    user would run (`python -m <your_package> …`, your console script, your
>    service). A target that chains `test lint coverage` re-runs dimensions the
>    gate has already scored and verifies nothing further.
> 2. **The step that does so must be able to fail.** `|| true`, a leading `-`,
>    and tool flags like `ruff --exit-zero` all keep a failure out of make's exit
>    code, which is the only thing the gate reads.
>
> Aim for a step that exercises a real acceptance criterion against real
> dependencies — a temporary database, a real file, the actual process — because
> the gate also measures which of your high-risk modules this target executed.
> Any module your test suite replaces with an `autouse` stand-in has to run for
> real here.
**Makefile target**: `verify-system`
**Exercises** (SPEC.md NFR-12, §8 row 27): chains, in order, (1) `alembic upgrade head`
against a real SQLite file (exercises `migrations/versions/v3_split_results.py`,
FR-07), (2) the full test suite, (3) starting the real ASGI service and smoke-testing
`GET /healthz` and `GET /readyz` (exercises `taskq_api.app`, `taskq_api.service.auth`
via a live API-key round trip, and `taskq_api.repository.session`'s pool/connectivity
check), (4) `alembic downgrade base` then `alembic upgrade head` again (round-trip
verification of the same v3 data-migration high-risk module). Exit 0 required; stdout
must print `verify-system: PASS`.

## 2. Module Design

### 2.1 Directory Structure Design Principles

> **CRG Architecture Scoring**: Phase 3+ judges your code's community cohesion via
> the Code Review Graph (CRG).  CRG groups files by **directory** — each directory
> is one community.  The architecture score is the fraction of communities that are
> "healthy" (internal edge density ≥ 0.3 AND size ≤ 50 nodes).
>
> **CRG scoring formula**: Each community's cohesion = internal_edges / (internal_edges + external_edges).
> External edges = calls to libraries (stdlib, frameworks) + calls to other communities.
> Internal edge dilution is the primary risk — entry points (CLI, main.py) import many libraries,
> producing external edges with no offsetting internal edges unless they also call sibling modules.
> The fix is **not** to reduce library imports — it is to ensure every function body also calls at least one
> sibling within the same directory.
>
> **Required edge budget**: To reach cohesion ≥ 0.3 with E external edges, you need
> I ≥ ceil(0.4286 × E) internal edges. Each function-body call to a hub function = 1 internal edge.
> Module-level calls create 1 edge per file, but per-function-body calls multiply the count.
> Example: 48 external edges → need ≥21 internal edges. With 5 sibling files each having
> 4 function bodies calling 2 hub functions → 40 internal edges — safely above threshold.

**Design for high cohesion from the start — 6 Universal CRG Design Principles:**

**Principle 1 — Use subdirectories to control CRG community boundaries.** CRG assigns one community per directory. If you dump 10+ files into a flat `src/`, CRG's Leiden algorithm freely splits them into unpredictable communities — some will likely fall below the 0.3 cohesion threshold. Explicit subdirectories (`src/api/`, `src/core/`, `src/infrastructure/`) each become one predictable community. Aim for 3-6 source directories total (excluding tests). Fewer than 3 → oversized single community; more than 6 → too many communities to keep all above 0.3.

**Principle 2 — Every directory needs a hub module (≥2 functions for 4+ siblings).** Each directory with ≥2 files must have a shared module (`utils.py`, `common.py`, `helpers.py`) that ≥70% of sibling files import and call via standalone function calls: `result = hub.fn(...)`. This creates cross-file internal edges. Pure library-utility files that no sibling calls produce zero internal edges — they only dilute the community.

For directories with ≥4 sibling files, **one hub function is rarely enough** — a single function called from 5 files produces ~5 edges, which may not offset ~40+ external edges. Use **≥2 hub functions** so each sibling can call both from multiple function bodies, multiplying internal edge count. The tts-new infrastructure directory (5 siblings, 48 external edges) required 2 hub functions (`validate_config` + `get_config_snapshot`) called from every function body to reach ~32 internal edges and pass 0.3.

Exception: directories that form a linear processing pipeline (A→B→C) where each file calls the next in chain.

**Principle 3 — Entry points must live inside a hub directory.** Entry-point modules (CLI, `main.py`, `app.py`, daemon) unavoidably import many external libraries — httpx, FastAPI, argparse, asyncio, etc. Each external import adds an external edge. If the entry point sits alone at the project root (e.g. `src/cli.py`), those external edges dominate and cohesion drops below 0.3. Place entry points inside a directory that also contains a hub module — the entry point calls the hub (internal edges) to compensate for its external edges.

**Principle 4 — Every function body must call a hub function (not just module-level).** A file that is never imported or called by any other file in its directory contributes only external edges (its own imports) and zero internal edges — pure dilution. For each file in your design, verify it is either: (a) the hub module itself, (b) called by the hub, or (c) calls the hub. Files that fail this check should be merged into another file or directory.

Critically, **module-level calls alone are insufficient**. A module-level `_ = validate_config()` creates 1 internal edge per file regardless of how many functions it has. CRG counts edges per (caller_node, callee_node) pair — each function body that calls the hub creates a separate edge. To accumulate enough internal edges (see edge budget above), the hub function must be called **from every accessible function body** in each sibling file, not just at module level. Example: a 5-sibling directory needs ~21 internal edges; 5 module-level calls + 5×4 function-body calls = 25 edges.

**Principle 5 — Respect CRG edge-detection limits.** CRG uses Tree-sitter AST parsing and detects cross-file function calls resolved through imports. These limitations are cross-language:
- Calls between functions in the **same** file — NOT detected (zero cohesion contribution)
- `self.method()` calls inside a class — DETECTED (class hierarchy contributes edges)
- `import sibling` → `sibling.fn()` — DETECTED (cross-file import resolved)
- `result = hub.fn(...)` then `log.info(..., extra=result)` — DETECTED (standalone assignment)
- `log.info(..., extra=hub.fn(...))` — INCONSISTENTLY detected (nested arg position)
- Calls through imports at runtime (lazy imports in `__getattr__`, `__init__.py` re-exports) — may be missed if not statically resolvable

**Principle 6 — Size cap: communities stay under 50 nodes.** CRG marks any community with >50 nodes as unhealthy regardless of cohesion. A node ≈ one function or class in a file. If your directory design would produce >50 nodes (roughly 4-6 modules with 8-12 functions each), split into subdirectories. Unlike Principles 1-5, this can be relaxed slightly — the cap is 50, not 30 — so this is rarely the binding constraint unless you have large god-modules.

| Quick reference | check |
|----------------|-------|
| Source directories count? | 3-6 |
| Each dir has a hub file? | Yes |
| Hub has ≥2 functions if ≥4 sibling files? | Yes |
| Entry points inside a hub dir? | Yes |
| Each function body calls a hub function? | Yes (not just module-level) |
| Cross-file calls use standalone assignment? | Yes |
| Community size ≤ 50 nodes? | Yes |
| Edge budget: I ≥ 0.4286 × E? | Yes |

**Anti-patterns that produce low scores:**

```
❌ src/__init__.py, src/main.py, src/models.py, src/cli.py, src/audio.py
   → 5 isolated files in flat src/, zero cross-imports → cohesion=0.0

❌ src/cli.py  (imports httpx, argparse, asyncio — all external, no internal sibling calls)
   → pure external edges, no compensation → cohesion near 0

❌ tests/test_fr01.py, tests/test_fr02.py, ... tests/test_fr08.py
   → 80 nodes in one dir, no internal edges → oversized + zero cohesion

✅ src/api/{cli,main,speech,utils}.py with utils imported by all siblings → hub-and-spoke
✅ src/engines/{synthesis,splitter,parser}.py with synthesis calling both → pipeline chain
✅ src/infrastructure/{circuit,health,config,models}.py → shared domain layer
```

### 2.2 Directory Structure

SPEC.md does not enumerate a standalone directory-tree section; the tree below
is derived from the layering contract (NFR-06), the high-risk module names
SPEC.md §10 already names literally (`taskq_api.service.runner`,
`taskq_api.service.auth`, `taskq_api.repository.session`,
`migrations/versions/v3_split_results.py`), and the module responsibilities
implied by each FR/NFR clause. 4 source directories (within the 3–6 target),
each ≤6 files (well under the 15/dir cap and the 50-node CRG community cap),
one hub per directory, entry points co-located with their hub.

```
03-development/src/taskq_api/
├── app.py               # ASGI app factory + lifespan (startup/graceful drain) — entry point
├── __main__.py          # CLI entry: migrate | seed | healthcheck | key create
├── config.py            # env var loading (independent — no project-internal imports)
├── exceptions.py        # domain exception hierarchy (independent)
├── redaction.py         # secret-pattern scrubbing for logs/errors (independent, NFR-04)
├── api/                 # hub: dependencies.py (imported by every route + error_handlers)
│   ├── routes_tasks.py
│   ├── routes_admin.py
│   ├── dependencies.py  # HUB — single auth+scope+rate-limit dependency (FR-04)
│   ├── error_handlers.py
│   └── schemas.py
├── service/             # hub: auth.py (imported by rate_limiter + tasks + runner)
│   ├── tasks.py
│   ├── runner.py
│   ├── auth.py          # HUB — key verification, scope hierarchy (FR-03/04)
│   ├── rate_limiter.py
│   └── metrics.py
├── repository/          # hub: session.py (imported by every *_repo.py)
│   ├── session.py       # HUB — Session/transaction boundary, pool config (FR-06)
│   ├── tasks_repo.py
│   ├── results_repo.py
│   ├── keys_repo.py
│   └── rate_repo.py
└── models/              # hub: task.py (task_tags/tags associate through it)
    ├── task.py           # HUB — tasks, tags, task_tags
    ├── api_key.py
    ├── task_result.py
    └── rate_bucket.py

migrations/versions/      # v1_initial.py, v2_tags.py, v3_split_results.py (FR-07)
```

No circular dependencies: `api → service → repository → models` is a strict
DAG (import-linter-enforced); `config`/`exceptions`/`redaction` sit outside
the DAG and are leaves (import nothing from the four layers), so nothing
cycles back into them.

### 2.3 Module-to-FR Traceability

| Module | Responsibility | Depends on | FR(s) |
|--------|----------------|-----------|-------|
| `api/routes_tasks.py` | `/v1/tasks*` route handlers (thin, ≤40 lines/handler — NFR-11) | `service.tasks`, `service.runner`, `api.schemas` | FR-01, FR-02 |
| `api/routes_admin.py` | `/healthz`, `/readyz`, `/v1/metrics` | `service.metrics`, `repository.session` | FR-09 |
| `api/dependencies.py` (**hub**) | single FastAPI dependency doing auth + scope + rate-limit for every `/v1` route (FR-04's "single dependency" requirement) | `service.auth`, `service.rate_limiter` | FR-03, FR-04, FR-05 |
| `api/error_handlers.py` | RFC 7807 problem+json exception handlers | `exceptions`, `redaction` | FR-10 |
| `api/schemas.py` | pydantic request/response models | — | FR-01 |
| `service/tasks.py` | task CRUD orchestration, name-uniqueness/validation | `repository.tasks_repo`, `service.auth` (hub) | FR-01 |
| `service/runner.py` | `asyncio.TaskGroup` executor, `create_subprocess_exec`, timeout+kill, graceful drain | `repository.results_repo`, `service.auth` (hub), `redaction` | FR-02, FR-08 |
| `service/auth.py` (**hub**) | key hash/verify (`hmac.compare_digest`), scope hierarchy check | `repository.keys_repo` | FR-03, FR-04 |
| `service/rate_limiter.py` | token-bucket algorithm | `repository.rate_repo`, `service.auth` (hub) | FR-05 |
| `service/metrics.py` | status counts, latency quantiles, rate-limit rejection counts | `repository.tasks_repo`, `service.auth` (hub) | FR-09 |
| `repository/session.py` (**hub**) | per-request `Session`, commit/rollback context manager, pool (`pool_size`, `pool_pre_ping`) | SQLAlchemy only | FR-06 |
| `repository/tasks_repo.py` | task CRUD queries, `selectinload`/`joinedload` (N+1 guard) | `repository.session` (hub), `models.task` | FR-01, FR-06 |
| `repository/results_repo.py` | `task_results` read/write | `repository.session` (hub), `models.task_result` | FR-02, FR-07 |
| `repository/keys_repo.py` | `api_keys` read/write, `revoked_at` check | `repository.session` (hub), `models.api_key` | FR-03 |
| `repository/rate_repo.py` | row-level-locked token-bucket persistence | `repository.session` (hub), `models.rate_bucket` | FR-05 |
| `models/task.py` (**hub**) | `tasks`, `tags`, `task_tags` ORM classes | SQLAlchemy only | FR-01, FR-07 |
| `models/api_key.py` | `api_keys` ORM class | `models.task` (hub, for shared declarative base) | FR-03 |
| `models/task_result.py` | `task_results` ORM class | `models.task` (hub) | FR-02, FR-07 |
| `models/rate_bucket.py` | `rate_buckets` ORM class | `models.task` (hub) | FR-05 |
| `migrations/versions/v1_initial.py` | create `tasks`, `api_keys` | Alembic only | FR-07 |
| `migrations/versions/v2_tags.py` | add `tags`, `task_tags`, unique index | Alembic only | FR-07 |
| `migrations/versions/v3_split_results.py` | split `tasks.result_json` → `task_results`, reversible data migration | Alembic only | FR-07 |
| `app.py` | app factory, lifespan-managed graceful drain | `api.*`, `service.runner` | FR-08, FR-09 |
| `__main__.py` | `migrate` / `seed` / `healthcheck` / `key create` CLI | `service.auth`, `repository.keys_repo` | FR-03 |

Every FR-01..FR-10 maps to ≥1 module above; no module exceeds the 40-line
handler cap (NFR-11) by design (business logic sits in `service/`, not
`api/`).

## 3. Interfaces & Data Flows

### 3.1 Synchronous request flow (FR-01, FR-03, FR-04, FR-05, FR-10)

```
Client
  │  HTTP + X-API-Key
  ▼
uvicorn (ASGI) ── app.py
  ▼
FastAPI routing ── api/routes_tasks.py | routes_admin.py
  ▼
api/dependencies.py  (single dependency: auth → scope → rate limit)
  │         │                    │
  │         ▼                    ▼
  │   service/auth.py     service/rate_limiter.py
  │   (hash+compare,      (token bucket via
  │    repository/         repository/rate_repo.py,
  │    keys_repo.py)        row-level lock)
  │
  ▼ (401/403/429 short-circuit here, before handler body runs — FR-04's
     "not leaked via resource lookup" requirement: auth/scope resolves
     before any resource query)
route handler (≤40 LOC) ── service/tasks.py
  ▼
repository/tasks_repo.py ── repository/session.py (Session, tx boundary)
  ▼
SQLAlchemy ORM ── models/task.py
  ▼
SQLite / PostgreSQL
  ▲
  │ on exception at any layer
api/error_handlers.py → RFC 7807 problem+json (redaction.py scrubs `detail`)
```

### 3.2 Async task-execution flow (FR-02, FR-08)

```
POST /v1/tasks/{id}/run  →  202 Accepted {run_id}
  │
  ▼
service/runner.py: asyncio.TaskGroup.create_task(...)
  │
  ▼
asyncio.create_subprocess_exec(*shlex.split(command))   # never shell=True
  │
  ├─ asyncio.wait_for(timeout=TASKQ_TASK_TIMEOUT)
  │     └─ on timeout: process.kill() → await process.wait()  (no orphans)
  │
  ├─ on asyncio.CancelledError: re-raise (never swallowed — NFR-03)
  │
  ▼
repository/results_repo.py → task_results row (exit_code, stdout_tail,
  stderr_tail redacted by redaction.py, duration_ms, finished_at)
  │
  ▼
GET /v1/tasks/{id}/runs → newest-first history
```

Service shutdown: `app.py` lifespan waits for in-flight `TaskGroup` members up
to `TASKQ_DRAIN_TIMEOUT`; timed-out runs are marked `interrupted` (FR-08).

### 3.3 Schema migration flow (FR-07, FR-09)

```
alembic upgrade head
  → migrations/versions/v1_initial.py   (tasks, api_keys)
  → migrations/versions/v2_tags.py      (tags, task_tags, unique index)
  → migrations/versions/v3_split_results.py
        (data migration: tasks.result_json → task_results, then drop column)
  ↓
GET /readyz  →  DB reachable AND `alembic current` == head → 200
             →  otherwise 503 fail-closed with a body naming which check failed
```

`downgrade` at every revision must be a real inverse (v3's downgrade migrates
`task_results` rows back into `tasks.result_json` before dropping the table),
verified by round-trip sample-data comparison (SPEC.md §8 row 12).

## 4. NFR Handling

| NFR | Concern | Module(s) | Handling |
|-----|---------|-----------|----------|
| NFR-01 (performance) | p95 latency, N+1 | `repository/tasks_repo.py` | `selectinload`/`joinedload` explicit eager loading; SQLAlchemy `event` listener counts SQL statements per request in tests to assert the count is constant regardless of row count; `pytest-benchmark` asserts p95 < 30ms (`GET /v1/tasks/{id}`) / < 80ms (list, limit 50) at 10k rows |
| NFR-02 (security) | authn/authz, injection, CORS | `service/auth.py`, `repository/*_repo.py`, `config.py` | keys SHA-256-hashed + `hmac.compare_digest`; all queries via ORM/parameterized statements (grep gate: 0 hits for string-built SQL); `TASKQ_CORS_ORIGINS` default-empty (deny-all); `bandit -r` gate at 0 HIGH/0 MEDIUM |
| NFR-03 (error handling / async correctness) | tx integrity, cancellation | `repository/session.py`, `service/runner.py` | context-manager-guaranteed commit-on-success/rollback-on-exception; no bare `except:`; `asyncio.CancelledError` explicitly re-raised, never caught by a bare `except Exception` |
| NFR-04 (sensitive-data masking) | secrets in logs/output | `redaction.py` | regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` replaces the whole matching line with `[REDACTED]` before any log/response write; applied in `service/runner.py` (stdout/stderr tails) and `api/error_handlers.py` (error `detail`) |
| NFR-05 (documentation) | docstring coverage | all modules | every public function/class carries a docstring referencing its owning `[FR-XX]`/`[NFR-XX]`; FastAPI auto-generates `/openapi.json` `summary`/`description` per route, asserted by test |
| NFR-06 (architecture constraints) | layering | project-root `.importlinter` | `api > service > repository > models` layers contract + forbidden-import rule (`sqlalchemy` only importable from `repository`/`models`); `lint-imports` must exit 0 |
| NFR-07 (license compliance) | dependency licensing | `requirements.txt` / `requirements.lock` (deployment artifacts, not a code module) | pinned direct deps + fully locked transitive tree; `pip-licenses --with-system` scanned against an MIT/BSD-2/BSD-3/Apache-2.0/PSF allowlist; SBOM emitted to `08-config/SBOM.json` |
| NFR-08 (mutation testing) | test strength | `service/`, `repository/` (scope-limited) | `mutmut run` scoped to these two layers per `harness_config.json`; score ≥ 70 |
| NFR-09 (test assertion quality) | zero-skip | `03-development/tests/` | no `skip`/`xfail`/assertion-free stubs anywhere, including the FR-07 migration tests, which run against a real SQLite file (not in-memory/mocked) |
| NFR-10 (integration coverage) | end-to-end paths | `03-development/tests/integration/` | driven via `httpx.AsyncClient(transport=ASGITransport(app))` (never calling handlers directly); ≥80% line coverage; covers full CRUD, every error code, migration round-trip, rate-limit trip/recovery, graceful drain |
| NFR-11 (readability) | maintainability index / CC | all modules | file ≤400 lines, directory ≤15 files (both satisfied by §2.2's tree), function CC ≤10, handler bodies ≤40 lines (business logic pushed into `service/`) |
| NFR-12 (execute verification target) | system-level proof | `Makefile` `verify-system` | see §1.2 — chains real migration, real test run, real service smoke test, real migration round-trip |

**Cost**: no NFR specifies a cost/budget target; SPEC.md's only quantitative
constraints are latency (NFR-01) and coverage/score thresholds — no cost
dimension is in scope for this round.

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-09-07"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.app"
        - name: "taskq_api.__main__"
        - name: "taskq_api.api.routes_tasks"
        - name: "taskq_api.api.routes_admin"
        - name: "taskq_api.api.dependencies"
        - name: "taskq_api.api.error_handlers"
        - name: "taskq_api.api.schemas"
      allowed_dependencies: ["service", "shared"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.rate_limiter"
        - name: "taskq_api.service.metrics"
      allowed_dependencies: ["repository", "shared"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.tasks_repo"
        - name: "taskq_api.repository.results_repo"
        - name: "taskq_api.repository.keys_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models", "shared"]
    - name: models
      modules:
        - name: "taskq_api.models.task"
        - name: "taskq_api.models.api_key"
        - name: "taskq_api.models.task_result"
        - name: "taskq_api.models.rate_bucket"
      allowed_dependencies: ["shared"]
    - name: shared  # config/exceptions/redaction — leaves, import nothing project-internal
      modules:
        - name: "taskq_api.config"
        - name: "taskq_api.exceptions"
        - name: "taskq_api.redaction"
      allowed_dependencies: []

  allowed_dependencies:
    - from: api
      to: service
    - from: api
      to: shared
    - from: service
      to: repository
    - from: service
      to: shared
    - from: repository
      to: models
    - from: repository
      to: shared
    - from: models
      to: shared

  quality_targets:
    max_complexity: 10
    min_coverage: 80
    max_coupling: 0.3

  nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance  # SRS.md:349
      target: "p95 < 30ms (GET /v1/tasks/{id}) / < 80ms (list, limit 50) at 10k rows"
      module: taskq_api.repository.tasks_repo
    NFR-02:
      type: security
      dimension: security  # SRS.md:380
      target: "bandit -r: 0 HIGH / 0 MEDIUM findings"
      module: taskq_api.service.auth
    NFR-03:
      type: reliability
      dimension: error_handling  # SRS.md:422
      target: "no bare except; asyncio.CancelledError always re-raised"
      module: taskq_api.repository.session
    NFR-04:
      type: security
      dimension: security  # SRS.md:464
      target: "0 unredacted secret-pattern hits in logs/responses"
      module: taskq_api.redaction
    NFR-05:
      type: documentation
      dimension: documentation  # SRS.md:491
      target: "100% public API docstring coverage citing FR/NFR ID"
      module: taskq_api
    NFR-06:
      type: layering
      dimension: architecture_constraints  # SRS.md:513
      target: "lint-imports exit 0 (zero layering-contract violations)"
      module: .importlinter
    NFR-07:
      type: licensing
      dimension: license_compliance  # SRS.md:544
      target: "0 non-allowlisted licenses (MIT/BSD-2/BSD-3/Apache-2.0/PSF only)"
      module: requirements.txt
    NFR-08:
      type: mutation
      dimension: mutation_testing  # SRS.md:577
      target: ">=70"
      module: taskq_api.service
      scope_layers: [service, repository]  # SRS AC-N8.3 — mutmut scope-limited to these two layers
    NFR-09:
      type: testability
      dimension: test_assertion_quality  # SRS.md:599
      target: "0 skipped/xfail tests, including FR-07 migration tests against a real SQLite file"
      module: 03-development/tests
    NFR-10:
      type: integration
      dimension: integration_coverage  # SRS.md:642
      target: ">=80"
      module: 03-development/tests/integration
    NFR-11:
      type: maintainability
      dimension: readability  # SRS.md:670
      target: "file <= 400 lines; function CC <= 10; handler bodies <= 40 lines"
      module: taskq_api
    NFR-12:
      type: verifiability
      dimension: execute_verification_target  # SRS.md:698
      target: "make verify-system exits 0 and prints verify-system: PASS"
      module: Makefile

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:
    FR-01: ["taskq_api.api.routes_tasks", "taskq_api.api.schemas", "taskq_api.service.tasks", "taskq_api.repository.tasks_repo", "taskq_api.models.task"]
    FR-02: ["taskq_api.api.routes_tasks", "taskq_api.service.runner", "taskq_api.repository.results_repo", "taskq_api.models.task_result"]
    FR-03: ["taskq_api.api.dependencies", "taskq_api.service.auth", "taskq_api.repository.keys_repo", "taskq_api.models.api_key", "taskq_api.__main__"]
    FR-04: ["taskq_api.api.dependencies", "taskq_api.service.auth"]
    FR-05: ["taskq_api.api.dependencies", "taskq_api.service.rate_limiter", "taskq_api.repository.rate_repo", "taskq_api.models.rate_bucket"]
    FR-06: ["taskq_api.repository.session", "taskq_api.repository.tasks_repo"]
    FR-07: ["taskq_api.repository.results_repo", "taskq_api.models.task", "taskq_api.models.task_result", "migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"]
    FR-08: ["taskq_api.service.runner", "taskq_api.app"]
    FR-09: ["taskq_api.api.routes_admin", "taskq_api.service.metrics", "taskq_api.repository.session", "taskq_api.app"]
    FR-10: ["taskq_api.api.error_handlers"]

  architecture_constraints:
    - "no_circular_dependencies"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"

  required_artifacts:  # repo-relative paths this project MUST ship
    # Checked against the delivered tree at every gate. A path that
    # is absent, or that ships somewhere other than where it is
    # declared, blocks and the message says which. Omit or leave []
    # if the spec names no mandatory files.
    - ".importlinter"
    - "requirements.txt"
    - "requirements.lock"
    - "requirements-dev.txt"
    - "alembic.ini"
    - "migrations/versions/"
    - ".env.example"
    - ".methodology/harness_config.json"
    - "Makefile"
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed
> by `core/quality_gate/security_design.py:extract_security_block()`.
> Do NOT hand-write the YAML — paste from the canonical template and
> replace EXAMPLE values with your project's real values.
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`
>
> `applicability: none` is a fully valid, honest declaration for a project
> with no real attack surface (e.g. a pure CLI formatting tool) — it
> requires a `justification` (>=20 chars) and skips the rest of this
> block. This is a decidable structural check, not a keyword scorer: an
> honest `none` always passes.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "unauthenticated HTTP ingress"
      description: "requests crossing from unauthenticated internet clients into api/dependencies.py before any identity is established"
    - id: TB-02
      name: "cross-scope privilege boundary"
      description: "an authenticated caller holding a lower scope (read/write) reaching a handler that requires a higher scope (write/admin)"
    - id: TB-03
      name: "task command to OS subprocess"
      description: "user-supplied task `command` string crossing from the API/DB into a real OS subprocess via service/runner.py"
    - id: TB-04
      name: "internal detail to external observer"
      description: "internal state (DB connection strings, stack traces, subprocess stdout/stderr) crossing from the process into an HTTP response body or a log sink a caller/operator can read"
    - id: TB-05
      name: "concurrent request to shared rate-limit state"
      description: "multiple concurrent requests for the same API key crossing into the shared `rate_buckets` row in the database"
  threats:
    - id: T-01
      boundary: TB-01
      category: spoofing
      description: "a caller with no key, an invalid key, or a revoked key attempts to be treated as an authenticated identity"
      mitigation: "X-API-Key required on every /v1/* route; key compared as SHA-256 hash via hmac.compare_digest (constant-time); revoked_at IS NOT NULL keys always rejected; missing/invalid -> 401"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t01_invalid_or_revoked_api_key_rejected"
    - id: T-02
      boundary: TB-02
      category: elevation_of_privilege
      description: "a write-scoped key calls an admin-only route (DELETE /v1/tasks/{id}, GET /v1/metrics) to act above its granted scope"
      mitigation: "scope check runs inside the single shared api/dependencies.py dependency, before the handler or any resource lookup executes; insufficient scope -> 403 with a body that does not reveal whether the target resource exists"
      owner_module: "taskq_api.api.dependencies"
      nfr: NFR-02
      verified_by: "test_sec_t02_insufficient_scope_rejected_without_leaking_existence"
    - id: T-03
      boundary: TB-03
      category: tampering
      description: "a task `command` value containing shell metacharacters (e.g. `; rm -rf`, backticks, `$()`) attempts to escape the intended single-executable invocation"
      mitigation: "execution uses asyncio.create_subprocess_exec(*shlex.split(command)) exclusively; shell=True is project-wide forbidden and grep-gated to 0 hits"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t03_shell_metacharacters_not_interpreted"
    - id: T-04
      boundary: TB-04
      category: information_disclosure
      description: "a 500 error body, a task's stdout/stderr tail, or a log line leaks a DB connection string, API token, or stack trace to a caller or log reader"
      mitigation: "redaction.py replaces any line matching the secret-pattern regex with [REDACTED] before it is logged or written into a response; RFC 7807 detail is built from a fixed whitelist, never from raw exception text"
      owner_module: "taskq_api.redaction"
      nfr: NFR-04
      verified_by: "test_sec_t04_secrets_redacted_from_response_and_logs"
    - id: T-05
      boundary: TB-05
      category: denial_of_service
      description: "two concurrent requests for the same key both read the token bucket before either writes, each believing capacity remains, and jointly exceed the intended burst limit"
      mitigation: "token-bucket read-modify-write happens inside one DB transaction holding a row-level lock on the key's rate_buckets row, serializing concurrent updates"
      owner_module: "taskq_api.repository.rate_repo"
      verified_by: "test_sec_t05_concurrent_requests_do_not_exceed_burst"
    - id: T-06
      boundary: TB-02
      category: repudiation
      description: "an authenticated caller who triggered a destructive action (task run, task delete) later denies having done so, with no way to trace the action back to a specific request"
      mitigation: "every response carries a correlation_id in both the X-Correlation-Id response header and the server log line for that request, linking any recorded action back to one traceable request"
      owner_module: "taskq_api.api.error_handlers"
      verified_by: "test_sec_t06_correlation_id_present_and_logged"
```
<!-- SEC:END -->

Note: `owner_module` must name a module declared in the §5 SAB block;
`nfr` (optional) must exist in SRS.md; `verified_by` names the test that
proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.
