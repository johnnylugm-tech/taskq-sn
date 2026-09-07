# Architecture Decision Records (ADR) — taskq-api

Source: derived from `02-architecture/SAD.md` (taskq-api Software Architecture
Document). Each entry below records one architecture decision, its context,
consequences, and alternatives considered.

## Traceability Matrix — Architecture Decisions → SRS.md Requirements

Per the architecture specification process, this table is the ADR-to-SRS
traceability matrix: it links every decision above to the functional
(`FR-NN`) and non-functional (`NFR-NN`) requirement(s) it satisfies, as
declared in `01-requirements/SRS.md`. A decision may serve more than one
requirement; a requirement enforced by project-wide process rather than a
single architectural choice is recorded as cross-cutting below, not forced
onto an ADR that does not actually decide it.

| ADR | Decision | FR Served | NFR Served |
|-----|----------|-----------|------------|
| ADR-001 | FastAPI ASGI framework + pydantic v2 | FR-01, FR-02, FR-04, FR-08 | NFR-05, NFR-10 |
| ADR-002 | Strict layered architecture, import-linter enforced | — | NFR-06, NFR-11 |
| ADR-003 | SQLAlchemy 2.x ORM, repository-owned sessions | FR-06 | NFR-01 |
| ADR-004 | Alembic reversible schema migration | FR-07 | NFR-09, NFR-12 |
| ADR-005 | `asyncio.TaskGroup` + `create_subprocess_exec` | FR-02, FR-08 | NFR-02, NFR-03 |
| ADR-006 | Single composed auth/scope dependency | FR-03, FR-04, FR-05 | NFR-02 |
| ADR-007 | DB-backed token-bucket rate limiting | FR-05 | NFR-01, NFR-03 |
| ADR-008 | RFC 7807 `problem+json` error contract | FR-10 | NFR-04 |
| ADR-009 | Single ORM model set (SQLite dev/test, PostgreSQL prod) | FR-06 | NFR-01 |
| ADR-010 | CRG-cohesive directory layout, Python 3.11+ | — | NFR-06, NFR-08 |
| — (cross-cutting; no owning ADR) | Dependency pinning, license allowlist, and SBOM generation are a process control (`pip-licenses`, `08-config/SBOM.json`), not an architectural decision this ADR set makes | — | NFR-07 |

---

## ADR-001: ASGI web framework — FastAPI

### Status
Accepted

### Context
SPEC.md requires an HTTP REST API with request/response schema validation
(FR-01), auto-generated API documentation (NFR-05), and a dependency-injection
point that can run auth + scope + rate-limit checks before every route handler
body executes (FR-04). The service also needs native `async def` handlers to
support out-of-band task execution (FR-02, FR-08).

### Decision
Use FastAPI (ASGI, `uvicorn taskq_api.app:app`) as the HTTP framework, with
pydantic v2 for request/response validation.

### Consequences
- Positive: dependency-injection system gives a single composable
  auth+scope+rate-limit dependency (`api/dependencies.py`) reused by every
  route, satisfying FR-04's "single dependency" requirement directly.
- Positive: OpenAPI/`  /openapi.json` generation is automatic, satisfying
  NFR-05's documentation requirement with no extra tooling.
- Positive: native `async def` routes compose with the `asyncio.TaskGroup`
  executor (ADR-005) without a sync/async bridging layer.
- Positive: because the app object is a real ASGI callable, integration
  tests drive it via `httpx.AsyncClient(transport=ASGITransport(app))`
  (NFR-10) with no separate test-server process to start or tear down.
- Negative: couples the project to FastAPI's dependency-injection idioms;
  swapping frameworks later would require rewriting `api/dependencies.py`
  and all route signatures.

### Alternatives considered
- **Flask + Flask-RESTX**: rejected — sync-only by default, would need a
  separate async worker bridge for FR-02/FR-08's background task execution.
- **Django REST Framework**: rejected — ORM and app-registry conventions
  conflict with the project's explicit `repository/`-only-owns-SQLAlchemy
  layering contract (ADR-002); pulls in far more framework surface than the
  ten FRs require.

---

## ADR-002: Strict layered architecture with machine-enforced boundaries

### Status
Accepted

### Context
NFR-06 requires the codebase to hold a hard layering contract so business
logic cannot leak database concerns into the API layer, and vice versa.
Without enforcement, layering conventions erode over time as handlers reach
directly into ORM sessions.

### Decision
Adopt a strict four-layer DAG — `api > service > repository > models` — where
upper layers may import lower layers but never the reverse. `config`,
`exceptions`, and `redaction` sit outside the DAG as leaf modules importable
by any layer. Only `repository/` and `models/` may import `sqlalchemy`. The
contract is enforced by `import-linter` (`.importlinter`), not left as a code
review convention.

### Consequences
- Positive: `lint-imports` gives a machine-checked, non-negotiable gate —
  a handler that imports SQLAlchemy directly fails CI immediately rather than
  surfacing as a review comment.
- Positive: business logic is provably confined to `service/`, keeping route
  handlers thin (NFR-11's ≤40-line handler cap is easier to hold because
  there is nowhere else to put the logic).
- Negative: every new cross-cutting concern must be placed in `config`,
  `exceptions`, or `redaction`, or it will violate the DAG — this constrains
  where shared utilities can live.

### Alternatives considered
- **Convention-only layering (no tool enforcement)**: rejected — NFR-06
  requires the constraint to be checked, not merely documented; conventions
  drift under time pressure.
- **Hexagonal/ports-and-adapters with explicit interface classes**: rejected
  as over-engineering for this round's scope — four FR-driven layers with a
  linear dependency direction cover the actual requirement (isolate
  SQLAlchemy from `service`/`api`) without introducing abstract base classes
  nothing else in the project needs.

---

## ADR-003: SQLAlchemy 2.x ORM with session ownership confined to `repository/`

### Status
Accepted

### Context
FR-06 requires all reads/writes to go through a layer that owns transaction
boundaries explicitly, and NFR-01 requires N+1 query patterns to be
preventable and testable. The service must also run against both SQLite
(dev/test) and PostgreSQL (prod) without dialect-specific ORM code.

### Decision
Use SQLAlchemy 2.x declarative ORM models (`models/`), with every `Session`
and transaction boundary created and closed inside `repository/session.py`
(the repository-layer hub) and its per-entity `*_repo.py` siblings. `service/`
and `api/` never import `sqlalchemy` directly (enforced by ADR-002).

### Decision detail
- `repository/session.py` provides a per-request `Session` inside a
  commit-on-success / rollback-on-exception context manager, with pool
  configuration (`pool_size`, `pool_pre_ping`) for connection health.
- `repository/tasks_repo.py` uses `selectinload`/`joinedload` explicitly to
  avoid N+1 queries — verified by a SQLAlchemy `event` listener that counts
  SQL statements per request in tests.

### Consequences
- Positive: one ORM model set runs unmodified against SQLite and PostgreSQL,
  keeping dev/test/prod parity without dialect branches.
- Positive: transaction integrity (commit/rollback) is guaranteed by a single
  context manager rather than repeated at each call site — reduces the
  surface for a forgotten `commit()`/`rollback()`.
- Negative: every new query path must go through `repository/`, adding one
  layer of indirection even for simple lookups.

### Alternatives considered
- **Raw SQL / a lightweight query builder**: rejected — loses the
  cross-dialect portability (SQLite dev/test vs PostgreSQL prod) that a
  single ORM model set gives for free, and would require hand-written N+1
  guards instead of `selectinload`/`joinedload`.
- **Session created per-layer (api or service owns it)**: rejected — this is
  precisely the leak NFR-06 forbids; confining `sqlalchemy` imports to
  `repository/`/`models/` is what makes the import-linter contract
  meaningful.

---

## ADR-004: Alembic for reversible schema migration

### Status
Accepted

### Context
FR-07 requires reversible schema evolution, including a migration that moves
data (not just a column add), and NFR-12's `verify-system` target must prove
a migration round-trip against a real database file.

### Decision
Use Alembic with three ordered revisions (`v1_initial`, `v2_tags`,
`v3_split_results`), each shipping a real `downgrade` — including v3's, which
migrates `task_results` rows back into `tasks.result_json` before dropping
the table, not a no-op or `raise NotImplementedError`.

### Consequences
- Positive: `alembic upgrade head` / `alembic downgrade base` / `alembic
  upgrade head` gives a verifiable round-trip that `verify-system` can
  execute against a real SQLite file, satisfying NFR-12's requirement that at
  least one gate step exercise the real entry point.
- Positive: `GET /readyz` can assert `alembic current == head`, giving a
  live signal that the running service's schema matches code expectations.
- Negative: v3's downgrade must carry real data-migration logic (row copy
  before column drop), which is more code than a schema-only downgrade and
  must be kept in sync with v3's upgrade path.

### Alternatives considered
- **Hand-rolled SQL migration scripts**: rejected — no built-in
  upgrade/downgrade pairing or revision graph; reversibility would have to be
  tracked manually, defeating FR-07's intent.
- **Migrations without a required `downgrade`**: rejected — SPEC.md §8 row 12
  requires round-trip verification by sample-data comparison; a forward-only
  migration can't be round-tripped.

---

## ADR-005: Out-of-band task execution via `asyncio.TaskGroup` + `create_subprocess_exec`

### Status
Accepted

### Context
FR-02 requires tasks to run as real OS processes without blocking the HTTP
response, FR-08 requires graceful drain on shutdown, and NFR-02/NFR-03
require the subprocess invocation to be immune to shell injection and to
never swallow cancellation.

### Decision
`POST /v1/tasks/{id}/run` returns `202 Accepted` immediately; the actual run
is scheduled via `asyncio.TaskGroup.create_task(...)` in `service/runner.py`,
which invokes `asyncio.create_subprocess_exec(*shlex.split(command))` —
`shell=True` is never used anywhere in the codebase, enforced by a grep gate.
Each run is wrapped in `asyncio.wait_for(timeout=TASKQ_TASK_TIMEOUT)`; on
timeout the process is killed (`process.kill()` then `await process.wait()`)
so no orphaned subprocess remains. `asyncio.CancelledError` is always
re-raised, never caught by a bare `except Exception`. On app shutdown, the
lifespan handler waits for in-flight `TaskGroup` members up to
`TASKQ_DRAIN_TIMEOUT`; any run still unfinished at that point is marked
`interrupted`.

### Consequences
- Positive: `create_subprocess_exec` with an argument list (never a shell
  string) closes the shell-injection class of vulnerability by construction,
  not by input sanitization — verified by `test_sec_t03_shell_metacharacters_not_interpreted`.
  A single grep gate over the codebase for `shell=True` catches any
  regression.
- Positive: `TaskGroup`'s structured concurrency guarantees a hung or
  cancelled child task cannot silently leak — the group either completes or
  propagates the cancellation, so a leaked subprocess or dangling task is
  structurally hard to introduce.
- Negative: `asyncio.TaskGroup` requires Python 3.11+; this pins the minimum
  interpreter version for the whole service (see ADR-010).
- Negative: an `interrupted` run has no automatic resume/retry — the FR/NFR
  set does not require one, so none is built (Simplicity First — no
  speculative retry logic for a requirement that doesn't exist).

### Alternatives considered
- **`subprocess.run(shell=True)` behind a `ThreadPoolExecutor`**: rejected —
  reintroduces the shell-injection surface `create_subprocess_exec` closes,
  and a thread pool has no structured-concurrency cancellation semantics
  equivalent to `TaskGroup`'s, making graceful drain (FR-08) harder to prove
  correct.
- **A separate worker process (e.g. a job queue with its own worker pool)**:
  rejected as out of scope — SPEC.md's FR-02/FR-08 describe an in-process
  background task, not a distributed job system; adding a worker/broker
  would be speculative infrastructure this round does not require.

---

## ADR-006: Authentication and authorization via a single composed dependency

### Status
Accepted

### Context
FR-03 requires API keys to never be stored or compared in plaintext, FR-04
requires scope enforcement that resolves before any resource lookup (so a
403 never leaks whether a target resource exists), and FR-05 requires rate
limiting layered on top of both.

### Decision
Every `/v1/*` route requires an `X-API-Key` header. Keys are stored as a
SHA-256 hash and compared with `hmac.compare_digest` (constant-time,
`service/auth.py`, `repository/keys_repo.py`). Auth, scope, and rate-limit
checks are composed into one FastAPI dependency (`api/dependencies.py`) that
runs before the route handler body, so a 401/403/429 short-circuits before
any resource query — satisfying FR-04's "not leaked via resource lookup"
requirement structurally rather than by handler-level discipline.

### Consequences
- Positive: no plaintext key is ever persisted; a database leak exposes only
  hashes, not usable credentials.
- Positive: because auth/scope/rate-limit resolve in one dependency ahead of
  the handler, there is no route-by-route risk of a handler author
  forgetting the scope check — the check is structurally unavoidable, not a
  per-handler convention.
- Negative: a single composed dependency means all three concerns (auth,
  scope, rate limit) must succeed or fail as one unit within one dependency
  function; testing each concern's error path in isolation requires
  exercising the full chain rather than a scope-only or rate-limit-only test
  double.

### Alternatives considered
- **Separate dependencies for auth, scope, and rate limit, chained per
  route**: rejected — leaves each route's handler author responsible for
  listing all three in the right order; a route that omits the scope
  dependency by mistake would compile and run with no scope check, which is
  exactly the elevation-of-privilege risk FR-04 exists to prevent.
- **JWT-based auth**: rejected — SPEC.md specifies a static per-client API
  key model (FR-03), not user sessions or token expiry/refresh; JWT would add
  claims-parsing and expiry logic the spec does not ask for.

---

## ADR-007: DB-backed token-bucket rate limiting with row-level locking

### Status
Accepted

### Context
FR-05 requires per-token rate limiting that stays consistent if the service
runs as multiple worker processes (i.e., limiter state cannot live only in
one process's memory).

### Decision
Implement a token-bucket algorithm in `service/rate_limiter.py`, with bucket
state persisted in a `rate_buckets` table and all read-modify-write cycles
performed inside one DB transaction holding a row-level lock on the calling
key's bucket row (`repository/rate_repo.py`).

### Consequences
- Positive: rate-limit state is correct across multiple worker processes,
  because the source of truth is the database, not per-process memory.
- Positive: the row-level lock serializes concurrent requests for the same
  key, closing the race where two concurrent requests both read
  "capacity available" before either writes — verified by
  `test_sec_t05_concurrent_requests_do_not_exceed_burst`.
- Negative: every rate-limited request now pays one DB round trip (a
  read-modify-write under lock) that an in-memory limiter would avoid —
  accepted as the cost of multi-worker correctness (FR-05).

### Alternatives considered
- **In-memory token bucket per process**: rejected — breaks under multiple
  worker processes, which FR-05 explicitly requires to stay consistent.
- **Redis-backed limiter**: rejected — introduces a new infrastructure
  dependency (a Redis instance to run, monitor, and fail over) for a
  requirement the existing relational database already satisfies via
  row-level locking; not justified for this round's scope.

---

## ADR-008: Uniform error contract via RFC 7807 problem+json

### Status
Accepted

### Context
FR-10 requires every non-2xx response to carry a consistent, detail-scrubbed
error body, and NFR-04 requires secrets never to leak into that body.

### Decision
All error responses use `application/problem+json` (RFC 7807), produced by
`api/error_handlers.py`. The `detail` field is built from a fixed whitelist
per exception type, never from raw exception text, and is passed through
`redaction.py` before being written to the response or a log line.

### Consequences
- Positive: every client-facing error has the same shape (`type`, `title`,
  `status`, `detail`, plus a `correlation_id` per ADR carried in
  `X-Correlation-Id`), simplifying client-side error handling.
- Positive: because `detail` is whitelist-built rather than derived from
  `str(exception)`, a stack trace or a DB connection string cannot reach a
  response body even if a future exception type is added carelessly.
- Negative: adding a new exception type requires explicitly authoring its
  whitelisted `detail` text — a raw passthrough is not permitted even for a
  new, seemingly harmless exception.

### Alternatives considered
- **Ad hoc `{"error": "..."}` bodies per exception**: rejected — gives no
  uniform shape for clients to parse, and provides no structural safeguard
  against a raw exception message reaching the response.
- **`detail` derived directly from `str(exception)`**: rejected — this is
  precisely the information-disclosure path (T-04 in SAD.md §6) NFR-04
  exists to close.

---

## ADR-009: Single ORM model set targeting SQLite (dev/test) and PostgreSQL (prod)

### Status
Accepted

### Context
The project needs a fast, dependency-free test/dev database while still
proving production behavior against PostgreSQL, without maintaining two
parallel sets of ORM models or dialect-specific query code.

### Decision
Define ORM models once (`models/`) using only SQLAlchemy constructs portable
across both dialects; SQLite backs dev/test, PostgreSQL backs prod. Alembic
migrations (ADR-004) run identically against both.

### Consequences
- Positive: tests run fast and locally with zero external database
  dependency, while the same model/migration code is what runs in
  production — no separate "test model" drift risk.
- Negative: any SQLAlchemy feature that behaves differently across SQLite and
  PostgreSQL (e.g., certain column types, `ON CONFLICT` semantics) must be
  avoided or explicitly guarded; this constrains the ORM feature set that can
  be used.

### Alternatives considered
- **SQLite everywhere, including production**: rejected — SQLite's
  single-writer model does not fit a multi-worker ASGI service under
  concurrent write load (task creation, rate-bucket updates).
  **PostgreSQL everywhere, including local dev/test**: rejected — adds a
  required external service to spin up before running the test suite, with
  no corresponding benefit for this round's scope.

---

## ADR-010: Directory layout as CRG-cohesive hub-and-spoke modules, Python 3.11+

### Status
Accepted

### Context
Module boundaries must reflect the layering contract (ADR-002) while keeping
each directory's internal call graph cohesive (SAD.md §2.1), and the async
executor (ADR-005) requires `asyncio.TaskGroup`, which needs Python 3.11 or
newer.

### Decision
Four source directories under `taskq_api/` — `api/`, `service/`,
`repository/`, `models/` — each with one hub module imported by its
siblings (`api/dependencies.py`, `service/auth.py`, `repository/session.py`,
`models/task.py`), plus three DAG-external leaf modules
(`config.py`, `exceptions.py`, `redaction.py`) and two entry points
(`app.py`, `__main__.py`) living alongside the `api/` hub. The target runtime
is Python 3.11 (verified locally: `.venv/bin/python --version` → Python
3.11.15), the minimum version providing `asyncio.TaskGroup`.

### Consequences
- Positive: every module's place in the `api > service > repository > models`
  DAG is unambiguous, and each directory has a clear hub any sibling can be
  understood in terms of.
- Positive: pinning Python 3.11+ is a direct, traceable consequence of
  ADR-005's `TaskGroup` requirement, not an arbitrary version choice.
- Positive: `service/` and `repository/` being the only directories holding
  business/persistence logic gives NFR-08's mutation-testing scope a
  boundary it can cite directly, with no separate module inventory needed
  to define which paths `mutmut` targets.
- Negative: deployment environments must guarantee Python ≥3.11; this rules
  out older LTS runtimes some hosting platforms still default to.

### Alternatives considered
- **A flat `taskq_api/` package with no subdirectories**: rejected — mixes
  API, business logic, persistence, and models in one namespace, which is
  exactly what ADR-002's layering contract exists to prevent, and gives no
  natural place to define a per-directory hub module.
- **`asyncio.wait`/callback-based concurrency (pre-3.11) instead of
  `TaskGroup`**: rejected — `TaskGroup`'s structured-concurrency guarantees
  (a child exception cancels siblings and propagates; the group cannot exit
  with an orphaned task) are what make FR-08's graceful-drain requirement
  provable; hand-rolled task bookkeeping would reintroduce the orphan risk
  `TaskGroup` closes by construction.
