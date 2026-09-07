# Software Requirements Specification (SRS) — taskq-api

> Canonical source: project-root `SPEC.md` (v1.0.0, 2026-07-30, 459 lines). 100% of
> `### FR-01`..`### FR-10` and `### NFR-01`..`### NFR-12` headings are transcribed
> below. No canonical clause is omitted; no clause not derivable from `SPEC.md` is
> introduced beyond the interpretive choices explicitly marked `DERIVED:`.

## 1. Requirements Overview

`taskq-api` is the HTTP-serviced evolution of a task-queue system (SPEC.md §1,
lines 49-55): a REST API to submit, query, and execute tasks, with relational
persistence, versioned schema migration, API-key authentication, per-token scope
authorization, and rate limiting. Language: Python 3.11. Runtime shape: an ASGI
service started as `uvicorn taskq_api.app:app`, plus a `python -m taskq_api`
management entrypoint (`migrate` / `seed` / `healthcheck`).

Technical architecture (SPEC.md §2, lines 58-74): FastAPI (ASGI) + pydantic v2
request/response models + SQLAlchemy 2.x ORM (declarative, explicit `Session`
transaction boundaries) + SQLite (dev/test) / PostgreSQL (prod) + Alembic
migrations (v1→v2→v3, each with a working `downgrade`) + `async def` endpoints
with an `asyncio.TaskGroup` background executor + `X-API-Key` header auth
(hashed, never plaintext) + per-token scope (`read`/`write`/`admin`) + per-token
token-bucket rate limiting + RFC 7807 `application/problem+json` error contract +
task execution via `asyncio.create_subprocess_exec` (no `shell=True`) + an
`import-linter` layering contract (NFR-06).

## 2. Constraints

- **C-1 — Language/runtime**: Python 3.11, ASGI via `uvicorn` (SPEC.md line 53-54).
- **C-2 — Execution model**: task execution MUST use
  `asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True` is
  forbidden (SPEC.md line 72, line 96).
- **C-3 — Layering contract**: `.importlinter` at project root enforces
  `api > service > repository > models`; no layer outside `repository` may
  import `sqlalchemy` (SPEC.md lines 73, 220-232 / NFR-06).
- **C-4 — Config surface**: 12 `TASKQ_*` environment variables, all declared in
  `.env.example` (SPEC.md §5.1, lines 287-303).

#### AC-C4.1

`grep -c "^TASKQ_" .env.example` returns exactly `12` — verified by SPEC.md §8
row 26 (line 382).

- **C-5 — Required project-side config files (non-optional)**: `.importlinter`,
  `requirements.txt` + `requirements.lock`, `requirements-dev.txt`,
  `alembic.ini` + `migrations/versions/`, `.env.example`,
  `.methodology/harness_config.json` (with `features.mutation_testing: true`,
  and `crg_cohesion_healthy` MUST NOT be downgraded), `Makefile` with a
  `verify-system` target (SPEC.md §5.3, lines 317-328).
- **C-6 — Database schema**: six tables (`tasks`, `api_keys`, `tags`,
  `task_tags`, `task_results`, `rate_buckets`) defined across the three Alembic
  revisions in FR-07 (SPEC.md §5.2, lines 304-315).

## 3. Functional Requirements

### FR-01: 任務資源 CRUD API

Canonical: SPEC.md lines 79-91.

#### AC-1.1

`POST /v1/tasks` (scope `write`) creates a task; the request body is validated
by the `TaskCreate` pydantic model — verified by SPEC.md §8 row 4 (valid write
key → 201 + task id, line 360).

#### AC-1.2

`GET /v1/tasks/{id}` (scope `read`) returns the full task record; an unknown
id returns HTTP 404 + `problem+json` — verified by §8 row 7 (line 363).

#### AC-1.3

`GET /v1/tasks` (scope `read`) is a paginated list supporting `?status=`,
`?limit=`, `?cursor=`. Pagination MUST be cursor-based, not offset-based.
Default `limit` is 50, max 200; exceeding the max → 422.

#### AC-1.4

`DELETE /v1/tasks/{id}` (scope `admin`) deletes the task together with its
result rows, in the same transaction.

#### AC-1.5

Validation rules match Round-1 FR-01: non-empty, ≤1000 characters,
injection-character blacklist, unique name; a violation returns HTTP 422 +
`problem+json`.

#### AC-1.6

A duplicate task name returns HTTP 409 — verified by §8 row 8 (line 364).

### FR-02: 任務執行端點

Canonical: SPEC.md lines 93-99.

#### AC-2.1

`POST /v1/tasks/{id}/run` (scope `write`) returns HTTP 202 Accepted with a
body containing `run_id`.

#### AC-2.2

Execution runs via `asyncio.create_subprocess_exec(*shlex.split(command))`,
`shell=True` forbidden, timeout = `TASKQ_TASK_TIMEOUT`.

#### AC-2.3

State machine: `pending → running → done | failed | timeout`.

#### AC-2.4

Execution result is written to the `task_results` table (FR-07 v3 schema):
`exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` / `finished_at`.

#### AC-2.5

`GET /v1/tasks/{id}/runs` (scope `read`) returns that task's run history,
newest-first.

### FR-03: API Key 認證

Canonical: SPEC.md lines 101-107.

#### AC-3.1

Every `/v1/*` endpoint requires an `X-API-Key` header; missing or invalid →
HTTP 401 + `problem+json` — verified by §8 row 5 (line 361).

#### AC-3.2

Keys are stored as a SHA-256 hash in the `api_keys` table, never plaintext;
comparison uses `hmac.compare_digest` (constant-time) — verified by §8 row 18
(no plaintext key; `key_hash` is 64 hex chars, line 374).

#### AC-3.3

Keys are created via `python -m taskq_api key create --scope <scope>`; the
plaintext value is printed exactly once, at creation time.

#### AC-3.4

A key with a non-null `revoked_at` is treated as invalid.

#### AC-3.5

`/healthz` and `/readyz` are exempt from authentication (FR-09).

### FR-04: Scope 授權

Canonical: SPEC.md lines 109-113.

#### AC-4.1

Each key carries one scope; hierarchy is inclusive: `read < write < admin`.

#### AC-4.2

Insufficient scope → HTTP 403 + `problem+json`; the body MUST NOT leak
whether the target resource exists — verified by §8 row 6 (non-admin write
key on `DELETE` → 403, no existence leak, line 362).

#### AC-4.3

Authorization MUST be decided in a single middleware dependency, not
scattered across handlers — verified by a test asserting every `/v1` route
passes through the same dependency.

### FR-05: 流量控制

Canonical: SPEC.md lines 115-120.

#### AC-5.1

Per-token token bucket: capacity `TASKQ_RATE_BURST`, refill rate
`TASKQ_RATE_PER_SEC`.

#### AC-5.2

Exceeding the limit → HTTP 429 + `problem+json` + `Retry-After` header
(seconds) — verified by §8 row 9 (line 365).

#### AC-5.3

Bucket state is persisted in the database (consistent across workers);
updates MUST occur inside a single transaction using a row-level lock.

#### AC-5.4

`/healthz` and `/readyz` are not rate-limited.

### FR-06: 持久化層與交易邊界

Canonical: SPEC.md lines 122-128.

#### AC-6.1

All data access goes through the `repository/` layer; the service layer MUST
NOT hold a `Session` directly.

#### AC-6.2

One `Session` per API request; commit on success, rollback on exception,
guaranteed via a context manager.

#### AC-6.3

String-concatenated SQL is forbidden; ORM or parameterized queries only (see
NFR-02) — verified by §8 row 17 (line 373).

#### AC-6.4

Relational queries use explicit eager loading (`selectinload` /
`joinedload`); N+1 is an acceptance-failure condition (see NFR-01) —
verified by §8 row 14 (line 370).

#### AC-6.5

Connection pool: `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`.

### FR-07: Schema Migration(Alembic 三步演進)

Canonical: SPEC.md lines 130-143.

#### AC-7.1

Three revisions, each with a working `downgrade`: v1 creates `tasks`,
`api_keys` (downgrade drops both); v2 adds `tags`, `task_tags`
(many-to-many) + a unique index on `tasks.name` (downgrade drops the new
tables/index without affecting v1 data); v3 (**data migration**) splits
`tasks.result_json` into a standalone `task_results` table, migrates
existing data, then drops the original column — downgrade migrates data back
to `tasks.result_json` then drops `task_results`, **no data may be lost**.

#### AC-7.2

`alembic upgrade head` and `alembic downgrade base` both succeed — verified
by §8 row 13 (exit 0, no leftover tables, line 369).

#### AC-7.3

Round-trip reversibility: `upgrade head` → write sample data → `downgrade -1`
→ `upgrade head` → sample data fields identical column-by-column (the v3
data migration is the focus of this criterion) — verified by §8 row 12
(line 368).

#### AC-7.4

Destructive shortcuts (e.g. `op.execute("DROP TABLE ...")`) are forbidden as
a substitute for a real `downgrade`.

#### AC-7.5

The migration files themselves are covered by tests (via Alembic's offline
SQL generation + assertions).

### FR-08: 非同步執行器

Canonical: SPEC.md lines 145-150.

#### AC-8.1

Background execution is managed by `asyncio.TaskGroup`; on shutdown,
graceful drain MUST wait for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`,
marking any that exceed it `interrupted` — verified by §8 row 25 (line 381).

#### AC-8.2

Concurrency is capped at `TASKQ_MAX_CONCURRENT`; tasks beyond the cap queue
rather than spawning unbounded coroutines.

#### AC-8.3

Task timeout is implemented with `asyncio.wait_for`; on timeout the
subprocess MUST be reliably terminated (`process.kill()` then
`await process.wait()`), leaving no orphan process — verified by §8 row 25.

#### AC-8.4

`asyncio.CancelledError` MUST propagate upward and MUST NOT be swallowed by
`except Exception` (see NFR-03).

### FR-09: 健康檢查與可觀測性

Canonical: SPEC.md lines 152-160.

#### AC-9.1

`GET /healthz` (no auth) → 200 `{"status":"ok"}` when the process is alive.

#### AC-9.2

`GET /readyz` (no auth) → 200 when the DB is reachable AND
`alembic current == head`; otherwise 503 with a body naming which check
failed — verified by §8 row 10 (DB down → 503, line 366) and row 11
(migration behind head → 503, line 367).

#### AC-9.3

`GET /v1/metrics` (scope `admin`) → task counts by status, execution-latency
percentiles, rate-limit rejection count.

#### AC-9.4

`/readyz` MUST fail closed when a migration has not been run against
newly-deployed code.

### FR-10: 錯誤契約(RFC 7807)

Canonical: SPEC.md lines 162-168.

#### AC-10.1

Every non-2xx response has `Content-Type: application/problem+json`.

#### AC-10.2

Body fields: `type` (URI), `title`, `status`, `detail`, `instance`,
`correlation_id`.

#### AC-10.3

`detail` MUST NOT leak internal detail: no SQL statements, stack traces,
file paths, or DB schema — verified by §8 row 19 (line 375).

#### AC-10.4

`correlation_id` appears both in the `X-Correlation-Id` response header and
in server logs.

#### AC-10.5

Error-code mapping: 422 validation / 401 unauthenticated / 403 insufficient
scope / 404 unknown resource / 409 name conflict / 429 rate limited / 503
not ready / 500 other.

## 4. Non-Functional Requirements

> Dimension roster check: every `dimension:` value below was grepped against
> the current `### <dimension>` headers in
> `harness/harness/ssi/prompts/evaluate_dimension.md`. All twelve canonical
> dimensions (`performance`, `security`, `error_handling`, `documentation`,
> `architecture_constraints`, `license_compliance`, `mutation_testing`,
> `test_assertion_quality`, `integration_coverage`, `readability`,
> `execute_verification_target`) are present in the current roster — no
> dimension notes are required.

### NFR-01: 效能與查詢效率

Canonical: SPEC.md lines 177-183. **dimension**: `performance`.

*Coverage note*: `evaluate_dimension.md`'s `performance` section scores
measured **mean** latency (`>1000ms → −25`, `>3000ms → −50`); it does not
read p95 and its thresholds are two orders of magnitude coarser than the
30ms/80ms this NFR demands (AC-N1.1/AC-N1.2 below). The dimension gate does
not verify those two ACs — they require dedicated `pytest-benchmark`
assertions per SPEC.md's own thresholds, tracked as a Phase 3+ implementation
task, not assumed covered by the Gate score.

#### AC-N1.1

`GET /v1/tasks/{id}` at 10,000 rows: p95 < 30ms (excluding network, measured
via ASGI transport) — verified by §8 row 15 (line 371) and §11 (line 437).

#### AC-N1.2

`GET /v1/tasks?limit=50` at 10,000 rows: p95 < 80ms — §11 (line 438).

#### AC-N1.3

N+1 is a failure condition: the SQL statement count for one list request
MUST be constant, independent of returned row count, asserted via a
SQLAlchemy event listener — verified by §8 row 14 (line 370).

#### AC-N1.4

Measurement tool: `pytest-benchmark`.

### NFR-02: HTTP 與資料層安全

Canonical: SPEC.md lines 185-194. **dimension**: `security`.

*Coverage note*: `evaluate_dimension.md`'s `security` section runs `bandit`
(severity-weighted score). Bandit's standard ruleset does not itself detect
SQL string-concatenation composition (AC-N2.2 below) — that check is the
dedicated grep gate SPEC.md §8 row 17 names, not a bandit finding, so
AC-N2.2 needs that separate grep gate rather than assuming the `security`
dimension covers it.

#### AC-N2.1

`shell=True` / `eval(` / `exec(` forbidden codebase-wide (0 grep hits) —
verified by §8 row 16 (line 372).

#### AC-N2.2

No string-concatenated SQL (f-string / `%` / `+` composed SQL); ORM or
parameterized only, verified by grep + code review — §8 row 17 (line 373).

#### AC-N2.3

API key hashed storage, `hmac.compare_digest` comparison (see FR-03) — §8
row 18.

#### AC-N2.4

403 response MUST NOT leak resource existence (see FR-04) — §8 row 6.

#### AC-N2.5

Error body MUST NOT contain stack/SQL/path (see FR-10) — §8 row 19.

#### AC-N2.6

CORS default-denies all origins; allowlist set via `TASKQ_CORS_ORIGINS`.

#### AC-N2.7

`bandit -r 03-development/src/`: 0 HIGH, 0 MEDIUM — §8 row 23 (line 379).

### NFR-03: 錯誤處理、交易與非同步正確性

Canonical: SPEC.md lines 196-204. **dimension**: `error_handling`.

*Coverage note*: `evaluate_dimension.md`'s `error_handling` section (v2.9)
flags `except_base_exception` — catching `BaseException` even with `raise`
— as an anti-pattern, which is a direct, well-aligned check for AC-N3.3
below. It does NOT, however, verify AC-N3.1 (context-manager
commit/rollback semantics), AC-N3.4 (no-unbounded-retry), or AC-N3.6
(migration-failure rollback) — the dimension only measures file-level
try/except presence and anti-pattern counts, not this transaction/retry
logic. Those three need dedicated integration tests, not assumed covered by
the dimension score.

#### AC-N3.1

Per-request transaction boundary is explicit: commit on success, rollback on
exception, guaranteed via context manager (see FR-06).

#### AC-N3.2

Bare `except:` and `except Exception: pass` are forbidden.

#### AC-N3.3

`asyncio.CancelledError` MUST NOT be swallowed — it MUST be re-raised.

#### AC-N3.4

A DB connection failure → `/readyz` 503 with an explicit `detail`; it MUST
NOT silently retry without bound.

#### AC-N3.5

Task timeout MUST reliably terminate the subprocess, leaving no orphan (see
FR-08).

#### AC-N3.6

A migration failure → transaction rollback; the database remains at the
previous revision (see FR-07).

### NFR-04: 敏感資料遮蔽

Canonical: SPEC.md lines 206-212. **dimension**: `security`.

*Coverage note*: `bandit` (the tool behind the `security` dimension) does not
check for the specific redaction regex in AC-N4.1 below or for
connection-string leakage in log/metrics output (AC-N4.2) — those need the
dedicated unit/integration test SPEC.md §8 row 20 names, not the bandit
gate.

#### AC-N4.1

`stdout_tail` / `stderr_tail` / logs / error bodies MUST have any line
matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
replaced whole-line with `[REDACTED]` before being persisted or emitted.

#### AC-N4.2

The database connection string (including password) MUST NOT appear in any
log, error message, or `/v1/metrics` response — verified by §8 row 20
(line 376).

#### AC-N4.3

The API-key plaintext is output only once, at `key create` time; it MUST NOT
be written to any persistent location.

### NFR-05: 文件覆蓋

Canonical: SPEC.md lines 214-218. **dimension**: `documentation`.

*Coverage note*: `evaluate_dimension.md`'s `documentation` section
(`ast-docstrings`) scores the presence of *any* docstring on public
defs/classes; it does not check that the docstring text cites an
`[FR-XX]`/`[NFR-XX]` tag, so AC-N5.1's citation requirement is not verified
by the dimension gate. Nor does that AST scan read the generated OpenAPI
schema, so AC-N5.2 (per-endpoint `summary`/`description`) also needs its own
dedicated test against `/openapi.json`, not the dimension score.

#### AC-N5.1

100% of public functions/classes have a docstring that cites `[FR-XX]` or
`[NFR-XX]`.

#### AC-N5.2

Every API endpoint has `summary` and `description` in the OpenAPI schema,
asserted against the FastAPI-generated `/openapi.json`.

### NFR-06: 架構分層契約

Canonical: SPEC.md lines 220-232. **dimension**: `architecture_constraints`.

*Coverage note*: `evaluate_dimension.md`'s `architecture_constraints`
section runs `lint-imports` against the declared contract and scores
exit-code (0→100), which matches these ACs directly, including the
forbidden-`sqlalchemy` rule (AC-N6.2) once declared as a forbidden contract
entry. No gap identified.

#### AC-N6.1

`.importlinter` MUST exist at the project root declaring
`api > service > repository > models`; upper layers may import lower
layers, lower layers MUST NOT import upper layers; `config` and `errors`
are independent modules.

#### AC-N6.2

Forbidden contract: no layer other than `repository` may import
`sqlalchemy`.

#### AC-N6.3

`lint-imports` MUST exit 0 — verified by §8 row 21 (line 377).

#### AC-N6.4

Passing MUST NOT be achieved by deleting `.importlinter`, wildcard
`ignore_imports`, or downgrading the contract.

### NFR-07: 依賴與授權合規

Canonical: SPEC.md lines 234-240. **dimension**: `license_compliance`.

*Coverage note*: `evaluate_dimension.md`'s `license_compliance` section runs
`scancode --license ... src/` — a source-code license-header scan, not a
dependency-tree scan and not an SBOM check. It does not verify
AC-N7.1/AC-N7.2/AC-N7.3 below (which require the `pip-licenses`
dependency-tree scan SPEC.md itself names) or AC-N7.4 (the SBOM artifact) —
these need a dedicated implementation/verification task, not the dimension
gate alone.

#### AC-N7.1

All runtime dependencies are pinned with `==` in `requirements.txt`;
transitive dependencies are fully locked via `requirements.lock`.

#### AC-N7.2

Allowed licenses: MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF; any
other license → that dependency MUST NOT be used.

#### AC-N7.3

The scan scope MUST include the full dependency tree (direct + transitive);
evidence command `pip-licenses --format=json --with-system` — verified by
§8 row 22 (line 378).

#### AC-N7.4

Produce an SBOM at `08-config/SBOM.json` containing each dependency's
`name` / `version` / `license` / `direct|transitive`.

### NFR-08: 變異測試

Canonical: SPEC.md lines 242-247. **dimension**: `mutation_testing`.

*Coverage note*: `evaluate_dimension.md`'s `mutation_testing` section covers
the `mutmut` protocol for Python; the ≥70 threshold and scope restriction
below are directly measurable by it. No gap identified.

#### AC-N8.1

`.methodology/harness_config.json` sets `features.mutation_testing: true`.

#### AC-N8.2

Mutation score ≥ 70 — verified by §8 row 24 (line 380).

#### AC-N8.3

Scope is limited to the `service/` and `repository/` layers, with the
limiting rationale (execution-time budget) recorded in
`harness_config.json`.

### NFR-09: 驗證真實性(零 skip 鐵律)

Canonical: SPEC.md lines 249-257. **dimension**: `test_assertion_quality`.

*Coverage note*: `evaluate_dimension.md`'s `test_assertion_quality` section
(`ast-assertions`) measures assertion density and zero-assert ratio only. It
does not itself read the `skipped` count from `pytest -q` output (AC-N9.2
below), check for the exclusion flags named in AC-N9.4, or verify that
FR-07's migration tests run against a real database rather than a mock
(AC-N9.5) — those three need dedicated CI checks beyond the dimension gate.

#### AC-N9.1

No FR/NFR verification test may be `pytest.skip` / `skipif` / `xfail`, or an
assertion-less stub.

#### AC-N9.2

`pytest 03-development/tests -q` skipped count MUST be 0 — verified by §8
row 1 (line 357).

#### AC-N9.3

Every test function has at least one `assert` (`zero_assert == 0`).

#### AC-N9.4

Anti-fraud clause: tests MUST NOT be excluded via `--ignore` / `-k` /
`--deselect` / `collect_ignore`, or by removing a directory from
`testpaths`.

#### AC-N9.5

FR-07's three-step migration MUST be tested against a real database (a
SQLite file, not an in-memory mock); round-trip reversibility is verified by
actual data comparison. It MUST NOT be downgraded to skip on the grounds
that "migration logic is too hard to test."

#### AC-N9.6

`TRACEABILITY_MATRIX.md`'s `VERIFIED` status is given only when the test
actually executed and passed.

### NFR-10: 整合覆蓋

Canonical: SPEC.md lines 259-264. **dimension**: `integration_coverage`.

*Coverage note*: `evaluate_dimension.md`'s `integration_coverage` section
measures only the aggregate line-coverage percentage of the source tree
while the integration suite runs. It does not verify the specific scenario
checklist in AC-N10.3 below — a suite could clear 80% coverage while missing
one of the required scenarios. That checklist needs dedicated test-inventory
tracking (e.g. in `TEST_SPEC.md`), not the coverage-percentage gate alone.

#### AC-N10.1

`03-development/tests/integration/` line coverage ≥ 80% — verified by §8
row 3 (line 359).

#### AC-N10.2

Integration tests are driven via
`httpx.AsyncClient(transport=ASGITransport(app))`; they MUST NOT call
handler functions directly.

#### AC-N10.3

Coverage MUST include at minimum: the full CRUD chain, one example each of
401/403/404/409/422/429/503, a migration round-trip, a rate-limit trigger
and recovery, and graceful drain.

### NFR-11: 可讀性

Canonical: SPEC.md lines 266-271. **dimension**: `readability`.

*Coverage note*: `evaluate_dimension.md`'s `readability` section scores only
the average MI (`radon mi`) across files. It does not enforce per-function
CC ≤ 10 (AC-N11.2 below), per-file/per-directory line or file-count caps
(AC-N11.3), or per-handler line limits (AC-N11.4) — a file could raise the
project's average MI while a single function still exceeds CC 10. These
need dedicated lint checks (e.g. `radon cc`, a line/file-count assertion),
not the MI-average gate alone.

#### AC-N11.1

Project MI (LLOC-weighted) ≥ 80.

#### AC-N11.2

Single-function cyclomatic complexity ≤ 10.

#### AC-N11.3

Single file ≤ 400 lines; single directory ≤ 15 files.

#### AC-N11.4

Each API handler ≤ 40 lines (business logic sinks to `service/`).

### NFR-12: 系統驗證目標

Canonical: SPEC.md lines 273-281. **dimension**: `execute_verification_target`.

*Coverage note*: `evaluate_dimension.md`'s `execute_verification_target`
section itself states the harness only checks that `make verify-system`
exits cleanly — "what the target *contains* is the project's own statement,
and the harness does not read it." So the dimension gate verifies AC-N12.2
below (exit 0 + PASS banner) but NOT AC-N12.1's four internal steps: a
`Makefile` that chains only some of the four steps can still score this
dimension 100. This is not a hypothetical — it is the exact hazard
`canonical_diff.py`'s own history records for this NFR (a prior project's
`verify-system` chained 2 of 4 required steps and still scored 100).
AC-N12.1 needs its own per-step assertions (e.g. the project's
`# NFR-12`-annotated tests actually exercising all four steps), not an
assumption that a green dimension score implies all four ran.

#### AC-N12.1

The `Makefile`'s `verify-system` target MUST chain: (1) `alembic upgrade
head`, (2) the full test suite, (3) service startup + `/healthz`/`/readyz`
smoke, (4) `alembic downgrade base` then `upgrade head` (round-trip
verification).

#### AC-N12.2

`make verify-system` MUST exit 0 and print `verify-system: PASS` to stdout
— verified by §8 row 27 (line 383).

## 5. Acceptance Criteria Summary

Every row of SPEC.md §8 (驗收標準, lines 351-384) maps onto the ACs above.
Rows 2 and 26 are project-wide gates that are not owned by a single FR/NFR
elsewhere in SPEC.md; they are listed here directly rather than forced onto
an unrelated requirement.

| SPEC §8 # | Command | Expected | Maps to |
|---|---|---|---|
| 1 | `pytest 03-development/tests -q` | all green, 0 skipped | AC-N9.2 |
| 2 | `pytest ... --cov=03-development/src --cov-report=term` | TOTAL 100% | project-wide line-coverage gate (framework default; not a dedicated SPEC NFR) |
| 3 | `pytest .../integration --cov=... --cov-report=term` | TOTAL ≥ 80% | AC-N10.1 |
| 4 | `POST /v1/tasks` (valid write key) | 201 + task id | AC-1.1 |
| 5 | `POST /v1/tasks` (no `X-API-Key`) | 401 + problem+json | AC-3.1 |
| 6 | `DELETE /v1/tasks/{id}` (write, non-admin) | 403, no existence leak | AC-4.2 / AC-N2.4 |
| 7 | `GET /v1/tasks/{unknown}` | 404 + problem+json | AC-1.2 |
| 8 | `POST /v1/tasks` duplicate name | 409 | AC-1.6 |
| 9 | requests beyond `TASKQ_RATE_BURST` | 429 + `Retry-After` | AC-5.2 |
| 10 | DB stopped, `GET /readyz` | 503, detail names DB | AC-9.2 |
| 11 | `alembic downgrade -1`, `GET /readyz` | 503, detail names migration | AC-9.2 / AC-9.4 |
| 12 | upgrade→write→downgrade -1→upgrade | sample data identical | AC-7.3 |
| 13 | `alembic downgrade base` | exit 0, no leftover tables | AC-7.2 |
| 14 | list endpoint SQL statement count (10k rows) | constant | AC-6.4 / AC-N1.3 |
| 15 | `GET /v1/tasks/{id}` p95 (10k rows) | < 30ms | AC-N1.1 |
| 16 | grep `shell=True\|eval(\|exec(` | 0 hits | AC-N2.1 |
| 17 | grep SQL string concatenation | 0 hits | AC-6.3 / AC-N2.2 |
| 18 | inspect `api_keys` table | no plaintext, 64-hex hash | AC-3.2 |
| 19 | trigger 500, inspect body | no stack/SQL/path | AC-10.3 / AC-N2.5 |
| 20 | logs + `/v1/metrics` full text | no DB-URL password | AC-N4.2 |
| 21 | `lint-imports` | exit 0, `sqlalchemy` import blocked | AC-N6.3 |
| 22 | `pip-licenses --format=json --with-system` | every license ∈ allowlist | AC-N7.3 |
| 23 | `bandit -r 03-development/src/` | 0 HIGH, 0 MEDIUM | AC-N2.7 |
| 24 | `mutmut run` / `mutmut results` | mutation score ≥ 70 | AC-N8.2 |
| 25 | shutdown with in-flight task | graceful drain, no orphan | AC-8.1 / AC-8.3 |
| 26 | `grep -c "^TASKQ_" .env.example` | 12 | AC-C4.1 |
| 27 | `make verify-system` | exit 0, prints `verify-system: PASS` | AC-N12.2 |

## 6. Out-of-Scope

SPEC.md does not mention any of the following; they are out-of-scope for this
SRS because no FR/NFR requires them (absence-based, not an invented
constraint):

- Authentication mechanisms other than the `X-API-Key` header (e.g. OAuth2,
  JWT, session cookies) — FR-03 defines the entire authentication surface.
- A UI/frontend client — SPEC.md §1-2 describe only the ASGI service and the
  `python -m taskq_api` management entrypoint.
- A distributed task broker/message queue — FR-08 executes tasks in-process
  via `asyncio.TaskGroup`; no external queue is named anywhere in SPEC.md.
- Multi-tenant isolation beyond per-key scope (FR-04) — no tenancy model is
  described.
- Infrastructure-as-code / deployment orchestration beyond the `Makefile`
  `verify-system` target and the documented environment variables (NFR-12,
  §5.1) — no container-orchestration or cloud-provider requirement appears
  in SPEC.md.

## 7. Open Issues

- **Prompt-injection scan**: SPEC.md was scanned for prompt-injection
  patterns before transcription; none were found. No clause was withheld and
  no `FR-XX-deferred` was required on that basis.
- **No canonical ambiguity requiring NFR-99**: SPEC.md's requirements are
  quantitative and precise throughout (explicit thresholds, exact regexes,
  named commands) — no phrase was found ambiguous enough to require an
  NFR-99 deferral or a `DERIVED:` interpretive marker.
- **Dimension-gate coverage gaps** (see per-NFR *Coverage note* above):
  NFR-01, NFR-02, NFR-04, NFR-05, NFR-07, NFR-09, NFR-10, NFR-11, and NFR-12
  each have at least one AC that the corresponding harness dimension gate
  does not verify on its own. Phase 3 onward MUST treat these ACs as needing
  a dedicated implementation/test task — a green dimension score for these
  NFRs does not, by itself, establish that the AC is met.
- **`canonical_diff.py` over_spec_score is not diagnostic for this SPEC.md**:
  `harness/scripts/canonical_diff.py --srs 01-requirements/SRS.md --spec
  SPEC.md --out srs_vs_spec_diff.json` was run per the Anti-Over-Spec
  Framework Evidence step and produced 94/117 clauses with
  `over_spec_score > 0.7`. Verified root cause: `_split_sentences()` first
  strips every fenced code block and every backtick span, then splits on
  `(?<=[.!?])\s+(?=[A-Z0-9])` — a boundary pattern that Chinese prose almost
  never contains and that removes SPEC.md's near-entirely backtick-quoted
  technical content beforehand. Measured directly: SPEC.md's 459 lines
  collapse to exactly **2** "canonical sentences" (7,406 and 5,641 chars),
  so essentially no English/code token in any AC can match a canonical
  sentence regardless of transcription fidelity, and the script's own
  `+0.3` interpretive-marker penalty (triggered by `must`/`should`/etc.,
  present in nearly every normative AC) pushes the residual score over 0.7
  even at ratios the tool's own `interpreted` threshold (`>= 0.45`) would
  otherwise treat as adequate. This is a tool/canonical-language mismatch
  (SPEC.md is Chinese-language with pervasive backtick-quoted terms), not
  evidence of invented or over-specified requirements — every AC above
  carries its own `SPEC.md` line/section citation as the actual,
  human-verifiable fidelity evidence. `srs_vs_spec_diff.json` is left on
  disk as produced, per the framework step; its per-AC scores should be read
  with this measured limitation in mind rather than acted on mechanically.

## 8. Risks

Canonical: SPEC.md §9 風險矩陣 (lines 387-402), transcribed verbatim.

| ID | 風險 | 影響 | 可能性 | 緩解 |
|----|------|------|--------|------|
| R1 | v3 資料搬遷遺失資料 | 高 | 中 | 往返可逆性測試以真實 DB 逐欄比對(FR-07 / AC-7.3) |
| R2 | SQL injection | 高 | 低 | 禁字串拼接 + ORM/參數化 + grep gate(NFR-02 / AC-N2.2) |
| R3 | API key 洩漏 | 高 | 中 | 雜湊儲存 + 常數時間比對 + 明文只印一次(FR-03 / AC-3.2/3.3) |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 授權判定在資源查詢之前(FR-04 / AC-4.2) |
| R5 | N+1 查詢在大表上崩潰 | 高 | 高 | 顯式預載 + SQL 計數斷言(NFR-01 / AC-N1.3) |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | RFC 7807 固定欄位 + detail 白名單(FR-10 / AC-10.3) |
| R7 | `CancelledError` 被吞 → 關閉時卡死 | 中 | 中 | 明文禁令 + 測試斷言(NFR-03 / AC-N3.3) |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | `kill()` + `await wait()`(FR-08 / AC-8.3) |
| R9 | 部署後忘記跑 migration | 高 | 中 | `/readyz` fail closed(FR-09 / AC-9.4) |
| R10 | 連線池耗盡 | 中 | 中 | `pool_pre_ping` + 併發上限(FR-06/FR-08) |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | lock 檔 + 全樹掃描(NFR-07 / AC-N7.3) |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 單一交易 + row-level lock(FR-05 / AC-5.3) |

## 9. Glossary

| Term | Definition |
|------|------------|
| API Key | `X-API-Key` header credential, stored hashed (SHA-256), never plaintext (FR-03). |
| Scope | Per-key permission tier: `read` < `write` < `admin`, hierarchical/inclusive (FR-04). |
| Token bucket | Rate-limiting algorithm with capacity `TASKQ_RATE_BURST` and refill rate `TASKQ_RATE_PER_SEC` (FR-05). |
| `problem+json` | RFC 7807 error response media type carrying `type`/`title`/`status`/`detail`/`instance`/`correlation_id` (FR-10). |
| Alembic revision | A versioned, reversible schema-migration step; this project has three (v1/v2/v3) (FR-07). |
| ASGI | Asynchronous Server Gateway Interface — the async web-server protocol FastAPI/uvicorn implement. |
| Graceful drain | Waiting for in-flight background tasks to finish (up to `TASKQ_DRAIN_TIMEOUT`) before shutdown, marking stragglers `interrupted` (FR-08). |
| Correlation ID | An identifier appearing in both the `X-Correlation-Id` response header and server logs, for cross-referencing (FR-10). |
| N+1 query | An anti-pattern where one logical list request issues a linearly-growing number of SQL statements; forbidden as an acceptance-failure condition (NFR-01). |
| SBOM | Software Bill of Materials — the dependency manifest required at `08-config/SBOM.json` (NFR-07). |
| MI (Maintainability Index) | LLOC-weighted readability proxy metric; required ≥ 80 (NFR-11). |
| Cyclomatic complexity (CC) | Count of independent paths through a function; required ≤ 10 per function (NFR-11). |
| Cursor-based pagination | Pagination keyed by an opaque cursor rather than a numeric offset, required for `GET /v1/tasks` (FR-01). |

## FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-09-07",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API: POST/GET/GET-list/DELETE /v1/tasks with scope-gated access, cursor-based pagination, and FR-01-style validation (non-empty, <=1000 chars, injection blacklist, unique name).",
      "implementation_functions": ["taskq_api.api.tasks.create_task", "taskq_api.api.tasks.get_task", "taskq_api.api.tasks.list_tasks", "taskq_api.api.tasks.delete_task"],
      "verification_method": "httpx.ASGITransport integration tests per SPEC.md SS8 rows 4/6/7/8"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint: POST /v1/tasks/{id}/run (202 Accepted) via asyncio.create_subprocess_exec with shlex-split command, no shell=True, state machine pending->running->done|failed|timeout, results written to task_results, and GET .../runs history.",
      "implementation_functions": ["taskq_api.api.tasks.run_task", "taskq_api.service.runner.execute", "taskq_api.api.tasks.list_runs"],
      "verification_method": "httpx.ASGITransport integration test asserting 202 + run_id, task_results row fields, and runs ordering"
    },
    {
      "id": "FR-03",
      "description": "API key authentication via X-API-Key header, SHA-256 hashed storage, hmac.compare_digest comparison, one-time plaintext print at creation, revoked_at invalidation, /healthz and /readyz exempt.",
      "implementation_functions": ["taskq_api.service.auth.verify_api_key", "taskq_api.api.dependencies.require_api_key"],
      "verification_method": "integration test per SPEC.md SS8 rows 5/18"
    },
    {
      "id": "FR-04",
      "description": "Per-token scope authorization (read<write<admin) enforced in a single dependency; insufficient scope -> 403 without leaking resource existence.",
      "implementation_functions": ["taskq_api.api.dependencies.require_scope"],
      "verification_method": "integration test per SPEC.md SS8 row 6 plus a route-introspection test asserting every /v1 route depends on require_scope"
    },
    {
      "id": "FR-05",
      "description": "Per-token token-bucket rate limiting (TASKQ_RATE_BURST/TASKQ_RATE_PER_SEC) persisted in DB with row-level lock, 429 + Retry-After on excess, /healthz and /readyz exempt.",
      "implementation_functions": ["taskq_api.service.rate_limit.check_and_consume", "taskq_api.repository.rate_buckets"],
      "verification_method": "integration test per SPEC.md SS8 row 9"
    },
    {
      "id": "FR-06",
      "description": "Persistence layer and transaction boundaries: repository-only Session access, per-request commit/rollback via context manager, no string-concatenated SQL, explicit eager loading, pooled connections.",
      "implementation_functions": ["taskq_api.repository.session.session_scope", "taskq_api.repository.tasks"],
      "verification_method": "grep gate per SPEC.md SS8 row 17 plus SQLAlchemy event-listener SQL-count assertion (row 14)"
    },
    {
      "id": "FR-07",
      "description": "Alembic three-step schema migration (v1 tasks/api_keys, v2 tags/task_tags+unique index, v3 result_json->task_results data migration) with reversible downgrades and round-trip data fidelity.",
      "implementation_functions": ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"],
      "verification_method": "real-SQLite round-trip test per SPEC.md SS8 rows 12/13 (NFR-09 real-DB requirement)"
    },
    {
      "id": "FR-08",
      "description": "Async background executor via asyncio.TaskGroup with concurrency cap, graceful drain on shutdown, asyncio.wait_for timeout with reliable subprocess kill, and CancelledError propagation.",
      "implementation_functions": ["taskq_api.service.runner.TaskExecutor", "taskq_api.service.runner.drain"],
      "verification_method": "integration test per SPEC.md SS8 row 25"
    },
    {
      "id": "FR-09",
      "description": "Health/readiness endpoints: /healthz liveness, /readyz DB+migration-head check (fail closed), /v1/metrics (admin scope) counts and latency percentiles.",
      "implementation_functions": ["taskq_api.api.health.healthz", "taskq_api.api.health.readyz", "taskq_api.api.metrics.get_metrics"],
      "verification_method": "integration test per SPEC.md SS8 rows 10/11"
    },
    {
      "id": "FR-10",
      "description": "RFC 7807 problem+json error contract for all non-2xx responses, with a whitelisted detail field (no stack/SQL/path) and correlation_id in header + logs.",
      "implementation_functions": ["taskq_api.errors.problem_json", "taskq_api.api.exception_handlers"],
      "verification_method": "integration test per SPEC.md SS8 row 19"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms and GET /v1/tasks?limit=50 p95 < 80ms at 10,000 rows; constant SQL statement count per list request (N+1 forbidden).",
      "test_method": "pytest-benchmark (SPEC.md SS8 row 15) + SQLAlchemy event-listener count assertion (row 14)"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "No shell=True/eval/exec, no SQL string concatenation, hashed API keys with constant-time comparison, no 403/error-body leakage, default-deny CORS, bandit 0 HIGH/0 MEDIUM.",
      "test_method": "grep gates (SS8 rows 16/17) + bandit -r (row 23)"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Explicit per-request transaction boundaries, no bare except/except-pass, CancelledError must propagate, no unbounded silent DB retry, reliable subprocess termination on timeout, migration failure rolls back.",
      "test_method": "ast-error-handling anti-pattern scan (except_base_exception) + dedicated integration tests for transaction/retry/migration-rollback behavior"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Redact secret-shaped substrings (sk-*, token=, Bearer, postgres(ql):// URLs) from stdout_tail/stderr_tail/logs/error bodies; DB connection string never appears in logs/metrics; API key plaintext never persisted.",
      "test_method": "unit test asserting redaction regex application + SPEC.md SS8 row 20 (log/metrics text scan)"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "100% public function/class docstring coverage citing [FR-XX]/[NFR-XX]; every API endpoint has OpenAPI summary+description.",
      "test_method": "ast-docstrings coverage scan + dedicated test asserting /openapi.json summary/description fields"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": ".importlinter enforces api>service>repository>models with sqlalchemy import forbidden outside repository; lint-imports must exit 0.",
      "test_method": "lint-imports (SPEC.md SS8 row 21)"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "All runtime deps ==-pinned with a full transitive lock file; only MIT/BSD-2/BSD-3/Apache-2.0/PSF licenses allowed across the full dependency tree; SBOM produced at 08-config/SBOM.json.",
      "test_method": "pip-licenses --format=json --with-system over the full tree (SPEC.md SS8 row 22) + SBOM presence/schema check"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "Mutation score >= 70 scoped to service/ and repository/ layers, with the scope restriction documented in harness_config.json.",
      "test_method": "mutmut run / mutmut results (SPEC.md SS8 row 24)"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "Zero pytest.skip/skipif/xfail/assertion-less tests; 0 skipped count; no test-exclusion flags; FR-07 migration tested against a real SQLite file, not a mock.",
      "test_method": "pytest 03-development/tests -q skipped-count check (SPEC.md SS8 row 1) + ast-assertions zero-assert scan"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration test line coverage >= 80%, driven only through httpx.ASGITransport, covering CRUD, every error code, migration round-trip, rate-limit trigger/recovery, and graceful drain.",
      "test_method": "pytest .../integration --cov=... --cov-report=term (SPEC.md SS8 row 3) + scenario checklist tracked in TEST_SPEC.md"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Project MI >= 80; per-function CC <= 10; file <= 400 lines; directory <= 15 files; API handlers <= 40 lines.",
      "test_method": "radon mi (project average) + dedicated radon cc / line-count / file-count lint checks"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "Makefile verify-system target chains alembic upgrade head, full test suite, health/ready smoke, and a downgrade/upgrade round-trip; must exit 0 and print verify-system: PASS.",
      "test_method": "make verify-system exit-code check (SPEC.md SS8 row 27) + per-step assertions confirming all four chained steps actually run"
    }
  ]
}
```
<!-- FR:END -->
