# Traceability Matrix — taskq-api

> Requirements Traceability Matrix
> Framework: harness-methodology
> Version: v1.0
> Phase: 1 — Requirements. No implementation exists yet (`03-development/src` and
> `03-development/tests` are empty). "Code" and "Test" columns below are
> **planned** mappings sourced verbatim from `01-requirements/SRS.md`'s FR Block
> (`implementation_functions` / `verification_method` / `test_method`), not
> observed file/line evidence. Per SRS.md AC-N9.6, a `VERIFIED` status is given
> only once a test has actually executed and passed — every row below is
> therefore `PLANNED`, not `VERIFIED`, until Phase 3+ produces real code/tests.

---

## Overview

Provides **FR/NFR -> SRS -> (planned) Code -> (planned) Test** bidirectional
traceability supporting ASPICE SWE.3/SYS.4 compliance. Sources:
`01-requirements/SRS.md` (22 requirements: FR-01..FR-10, NFR-01..NFR-12) and
`01-requirements/SPEC_TRACKING.md` (Intent Class / Decision Framework / Status).
Test Case IDs are the naming authority of `TEST_INVENTORY.yaml` (`tc_id` /
`test_function` schema); as of this round that file at the project root still
holds only its four illustrative placeholder entries (`TC-FR01-01`,
`TC-FR01-02`, `TC-N02-01`, `TC-N09-01`) and has not yet been populated for
taskq-api's full FR-01..NFR-12 set — the "Test Case Naming" column below cites
the verification *method*, not a `tc_id`, until that file is filled in.

---

## 1. FR/NFR <-> SRS Mapping

| ID | Requirement | SRS Section | Key ACs | SPEC.md §8 Rows | Priority | Status |
|----|-------------|-------------|---------|-----------------|----------|--------|
| FR-01 | 任務資源 CRUD API (`/v1/tasks` create/read/list/delete) | SRS §3 FR-01 | AC-1.1–AC-1.6 | 4, 7, 8 | HIGH | DRAFT |
| FR-02 | 任務執行端點 (`POST /v1/tasks/{id}/run`, run history) | SRS §3 FR-02 | AC-2.1–AC-2.5 | **none** ⚠ | HIGH | DRAFT |
| FR-03 | API Key 認證 (`X-API-Key`, hashed storage) | SRS §3 FR-03 | AC-3.1–AC-3.5 | 5, 18 | HIGH | DRAFT |
| FR-04 | Scope 授權 (read/write/admin) | SRS §3 FR-04 | AC-4.1–AC-4.3 | 6 | HIGH | DRAFT |
| FR-05 | 流量控制 (token bucket rate limit) | SRS §3 FR-05 | AC-5.1–AC-5.4 | 9 | HIGH | DRAFT |
| FR-06 | 持久化層與交易邊界 (repository/Session/eager load) | SRS §3 FR-06 | AC-6.1–AC-6.5 | 14, 17 | HIGH | DRAFT |
| FR-07 | Schema Migration (Alembic v1→v2→v3) | SRS §3 FR-07 | AC-7.1–AC-7.5 | 12, 13 | HIGH | DRAFT |
| FR-08 | 非同步執行器 (`asyncio.TaskGroup`, drain, timeout) | SRS §3 FR-08 | AC-8.1–AC-8.4 | 25 | HIGH | DRAFT |
| FR-09 | 健康檢查與可觀測性 (`/healthz`, `/readyz`, `/v1/metrics`) | SRS §3 FR-09 | AC-9.1–AC-9.4 | 10, 11 | HIGH | DRAFT |
| FR-10 | 錯誤契約 (RFC 7807 `problem+json`) | SRS §3 FR-10 | AC-10.1–AC-10.5 | 19 | HIGH | DRAFT |
| NFR-01 | 效能與查詢效率 (p95 latency, N+1 forbidden) | SRS §4 NFR-01 | AC-N1.1–AC-N1.4 | 14, 15 | HIGH | DRAFT |
| NFR-02 | HTTP 與資料層安全 (no `shell=True`, no SQL concat) | SRS §4 NFR-02 | AC-N2.1–AC-N2.7 | 6, 16, 17, 18, 19, 23 | HIGH | DRAFT |
| NFR-03 | 錯誤處理、交易與非同步正確性 | SRS §4 NFR-03 | AC-N3.1–AC-N3.6 | **none** ⚠ | HIGH | DRAFT |
| NFR-04 | 敏感資料遮蔽 (secret redaction) | SRS §4 NFR-04 | AC-N4.1–AC-N4.3 | 20 | HIGH | DRAFT |
| NFR-05 | 文件覆蓋 (docstrings, OpenAPI summary) | SRS §4 NFR-05 | AC-N5.1–AC-N5.2 | **none** ⚠ | MEDIUM | DRAFT |
| NFR-06 | 架構分層契約 (`.importlinter`) | SRS §4 NFR-06 | AC-N6.1–AC-N6.4 | 21 | HIGH | DRAFT |
| NFR-07 | 依賴與授權合規 (licenses, SBOM) | SRS §4 NFR-07 | AC-N7.1–AC-N7.4 | 22 | MEDIUM | DRAFT |
| NFR-08 | 變異測試 (mutmut ≥ 70) | SRS §4 NFR-08 | AC-N8.1–AC-N8.3 | 24 | HIGH | DRAFT |
| NFR-09 | 驗證真實性 (零 skip 鐵律) | SRS §4 NFR-09 | AC-N9.1–AC-N9.6 | 1 | HIGH | DRAFT |
| NFR-10 | 整合覆蓋 (≥80% integration coverage) | SRS §4 NFR-10 | AC-N10.1–AC-N10.3 | 3 | HIGH | DRAFT |
| NFR-11 | 可讀性 (MI≥80, CC≤10) | SRS §4 NFR-11 | AC-N11.1–AC-N11.4 | **none** ⚠ | MEDIUM | DRAFT |
| NFR-12 | 系統驗證目標 (`make verify-system`) | SRS §4 NFR-12 | AC-N12.1–AC-N12.2 | 27 | HIGH | DRAFT |

Priority: SRS.md/SPEC.md do not define a differentiated priority tier per
FR/NFR — every item carries a dedicated acceptance criterion and is treated as
HIGH; NFR-05/NFR-07/NFR-11 are marked MEDIUM only because their SRS *Coverage
note* already documents the dimension gate as non-blocking for their residual
ACs (a lint/report gap, not a functional risk). This MEDIUM/HIGH split is this
matrix's own working inference, not a value stated in SPEC.md/SRS.md.

⚠ = no SPEC.md §8 row cites this requirement's ACs directly (see §4 Coverage
Gaps below) — this is a genuine traceability gap, not an omission in this
matrix: SRS §5's own summary table (which transcribes every §8 row) contains
no `AC-2.x` / `AC-N3.x` / `AC-N5.x` / `AC-N11.x` entry.

---

## 2. SRS <-> Planned-Code Mapping

Module paths are copied verbatim from SRS.md's `FR Block` (`implementation_functions`);
exact file layout is a Phase 2 decision owned by `02-architecture/SAD.md`
(currently a template stub, not yet authored) and may shift.

| ID | Planned Module(s) | Code Status |
|----|--------------------|-------------|
| FR-01 | `taskq_api.api.tasks.{create_task,get_task,list_tasks,delete_task}` | PLANNED — no source exists |
| FR-02 | `taskq_api.api.tasks.run_task`, `taskq_api.service.runner.execute`, `taskq_api.api.tasks.list_runs` | PLANNED — no source exists |
| FR-03 | `taskq_api.service.auth.verify_api_key`, `taskq_api.api.dependencies.require_api_key` | PLANNED — no source exists |
| FR-04 | `taskq_api.api.dependencies.require_scope` | PLANNED — no source exists |
| FR-05 | `taskq_api.service.rate_limit.check_and_consume`, `taskq_api.repository.rate_buckets` | PLANNED — no source exists |
| FR-06 | `taskq_api.repository.session.session_scope`, `taskq_api.repository.tasks` | PLANNED — no source exists |
| FR-07 | `migrations.versions.{v1_initial,v2_tags,v3_split_results}` | PLANNED — no source exists |
| FR-08 | `taskq_api.service.runner.{TaskExecutor,drain}` | PLANNED — no source exists |
| FR-09 | `taskq_api.api.health.{healthz,readyz}`, `taskq_api.api.metrics.get_metrics` | PLANNED — no source exists |
| FR-10 | `taskq_api.errors.problem_json`, `taskq_api.api.exception_handlers` | PLANNED — no source exists |
| NFR-01..12 | cross-cutting (no single module — enforced across the layers above) | PLANNED — no source exists |

---

## 3. Planned-Code <-> Test Mapping

`Verification/Test Method` is copied verbatim from SRS.md's FR Block. No test
file exists yet (`03-development/tests` is empty); once
`01-requirements/TEST_INVENTORY.yaml` is populated with real `tc_id`/
`test_function` entries for this project, this column should be updated to
cite those IDs directly.

| ID | Verification / Test Method (from SRS.md) | SPEC.md §8 Rows | Status |
|----|-------------------------------------------|-----------------|--------|
| FR-01 | httpx `ASGITransport` integration tests | 4, 7, 8 | PLANNED — no test exists |
| FR-02 | httpx `ASGITransport` integration test (202 + `run_id`, `task_results` fields, run ordering) | **none** ⚠ | PLANNED — no test exists |
| FR-03 | integration test | 5, 18 | PLANNED — no test exists |
| FR-04 | integration test + route-introspection test (every `/v1` route depends on `require_scope`) | 6 | PLANNED — no test exists |
| FR-05 | integration test | 9 | PLANNED — no test exists |
| FR-06 | grep gate + SQLAlchemy event-listener SQL-count assertion | 14, 17 | PLANNED — no test exists |
| FR-07 | real-SQLite round-trip test (NFR-09 real-DB requirement) | 12, 13 | PLANNED — no test exists |
| FR-08 | integration test | 25 | PLANNED — no test exists |
| FR-09 | integration test | 10, 11 | PLANNED — no test exists |
| FR-10 | integration test | 19 | PLANNED — no test exists |
| NFR-01 | `pytest-benchmark` + SQLAlchemy event-listener count assertion | 14, 15 | PLANNED — no test exists |
| NFR-02 | grep gates + `bandit -r` | 6, 16, 17, 18, 19, 23 | PLANNED — no test exists |
| NFR-03 | `error_handling` dimension anti-pattern scan + dedicated integration tests (transaction/retry/migration-rollback) | **none** ⚠ | PLANNED — no test exists |
| NFR-04 | unit test (redaction regex) + log/metrics text scan | 20 | PLANNED — no test exists |
| NFR-05 | `ast-docstrings` scan + dedicated `/openapi.json` field test | **none** ⚠ | PLANNED — no test exists |
| NFR-06 | `lint-imports` | 21 | PLANNED — no test exists |
| NFR-07 | `pip-licenses --format=json --with-system` + SBOM presence/schema check | 22 | PLANNED — no test exists |
| NFR-08 | `mutmut run` / `mutmut results` | 24 | PLANNED — no test exists |
| NFR-09 | `pytest ... -q` skipped-count check + `ast-assertions` zero-assert scan | 1 | PLANNED — no test exists |
| NFR-10 | `pytest .../integration --cov=...` + scenario checklist (owned by `02-architecture/TEST_SPEC.md`) | 3 | PLANNED — no test exists |
| NFR-11 | `radon mi` + dedicated `radon cc` / line-count / file-count checks | **none** ⚠ | PLANNED — no test exists |
| NFR-12 | `make verify-system` exit-code check + per-step assertions | 27 | PLANNED — no test exists |

---

## 4. Coverage Gaps (bidirectional validation findings)

- **FR-02 has no SPEC.md §8 acceptance-criteria row.** SRS §5's own transcription
  of all 27 rows contains no `AC-2.x` entry — the `POST /v1/tasks/{id}/run`
  202/state-machine/`task_results`-write behavior is exercised only by the
  general integration-coverage requirement (NFR-10 / AC-N10.3's "full CRUD
  chain" checklist item), not a dedicated acceptance row. **Recommendation**:
  04-testing should still write a dedicated test per AC-2.1–AC-2.5 even though
  no SPEC §8 row demands one by number — the requirement itself (SPEC.md lines
  93-99) is unambiguous.
- **NFR-03, NFR-05, NFR-11 similarly have no dedicated SPEC.md §8 row** — this
  matches SRS.md §7's own "Dimension-gate coverage gaps" note: their harness
  dimension (`error_handling`, `documentation`, `readability`) each measure a
  proxy (anti-pattern count / docstring presence / average MI) that does not
  verify every AC (e.g. AC-N3.1 transaction semantics, AC-N5.1's FR/NFR
  citation text, AC-N11.2 per-function CC). These already carry a *Coverage
  note* in SRS.md §4 flagging the same gap; this matrix does not duplicate
  that analysis, only cross-references it.
- **`TEST_INVENTORY.yaml` (project root) is not yet populated** for this
  project's FR-01..NFR-12 set — it holds only 4 illustrative placeholder
  `tc_id` rows (`TC-FR01-01`, `TC-FR01-02`, `TC-N02-01`, `TC-N09-01`). Once
  filled in, §3 above should be re-keyed from "Verification Method" text to
  actual `tc_id` values for exact 1:1 traceability.

---

## 5. Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR/NFR -> SRS mapping | 100% | 22/22 (100%) | ✅ PASS |
| FR/NFR -> SPEC.md §8 row mapping | 100% | 18/22 (82%) — FR-02, NFR-03, NFR-05, NFR-11 have no direct row | ⚠ GAP (see §4) |
| SRS -> Code mapping (documented/planned) | 100% | 22/22 (100%) | ✅ PASS (planned only — see note below) |
| SRS -> Code mapping (implemented) | — | 0/22 (0%) | expected — Phase 1, no source exists yet |
| Code -> Test mapping (implemented & executed) | >=80% (P3: >=70%) | 0% | expected — Phase 1, no tests exist yet |

Note: "SRS -> Code mapping (documented/planned)" measures whether every
FR/NFR has a named planned module in SRS.md's FR Block — it is a documentation
completeness check, not a claim that code exists. Per AC-N9.6, no row in this
matrix may be marked `VERIFIED` until a test has actually executed and passed;
all rows above are correctly `DRAFT`/`PLANNED` for Phase 1.

---

## 6. ASPICE Compliance

| ASPICE Capability | Status |
|-------------------|--------|
| SWE.3.B.SP1 Task-to-work-product traceability | PARTIAL — FR/NFR ↔ SRS section links established (§1); FR/NFR ↔ code/test links are planned-only (§2–§3), pending Phase 3 implementation |
| SWE.3.B.SP2 Bidirectional traceability | PARTIAL — SRS → SPEC.md §8 traced in both directions where a row exists (18/22); 4 requirements (FR-02, NFR-03, NFR-05, NFR-11) currently trace forward only (SRS → AC) with no §8 row to trace backward from (§4) |
| SWE.3.B.SP3 Traceability consistency | ESTABLISHED — all IDs cross-checked against `SRS.md`'s FR Block (machine-readable JSON) and `SPEC_TRACKING.md`; no ID mismatch found |
