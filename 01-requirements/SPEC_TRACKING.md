# Specification Tracking Matrix — taskq-api

> On-demand Lazy Load template.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Created: 2026-09-07
- Canonical spec source: `SPEC.md` (root; 459 lines, v1.0.0, 2026-07-30) — transcribed in full into `01-requirements/SRS.md`.

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework / Notes);
> leave Status to refresh itself (a hand-edit is overwritten on the next advance).

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| FR-01 | 任務資源 CRUD API — create/read/list/delete `/v1/tasks` | Core Resource CRUD | AC-driven verification (AC-1.1–AC-1.6) against `SPEC.md` §8 rows 4/7/8 | DRAFT | Cursor-based pagination (not offset); duplicate name → 409 |
| FR-02 | 任務執行端點 — `POST /v1/tasks/{id}/run` + run history | Async Execution / Command API | AC-driven verification (AC-2.1–AC-2.5); subprocess exec constraint C-2 | DRAFT | `shell=True` forbidden; state machine pending→running→done/failed/timeout |
| FR-03 | API Key 認證 — `X-API-Key` header, hashed storage | AuthN / Security | AC-driven verification (AC-3.1–AC-3.5) against `SPEC.md` §8 rows 5/18 | DRAFT | SHA-256 hash, `hmac.compare_digest`; `/healthz`,`/readyz` exempt |
| FR-04 | Scope 授權 — read/write/admin hierarchy | AuthZ / Security | AC-driven verification (AC-4.1–AC-4.3) against `SPEC.md` §8 row 6 | DRAFT | Single middleware dependency; 403 must not leak resource existence |
| FR-05 | 流量控制 — per-token token bucket rate limiting | Rate Limiting / Traffic Control | AC-driven verification (AC-5.1–AC-5.4) against `SPEC.md` §8 row 9 | DRAFT | Bucket state persisted in DB with row-level lock; exempts health endpoints |
| FR-06 | 持久化層與交易邊界 — repository layer, one Session per request | Persistence / Transaction Boundary | AC-driven verification (AC-6.1–AC-6.5) against `SPEC.md` §8 rows 14/17 | DRAFT | No raw string-concatenated SQL; explicit eager loading (N+1 forbidden) |
| FR-07 | Schema Migration — Alembic v1→v2→v3, each with working downgrade | Schema Migration | AC-driven verification (AC-7.1–AC-7.5) against `SPEC.md` §8 rows 12/13; must run against a real DB (see AC-N9.5) | DRAFT | v3 is a data migration (`result_json` → `task_results`); no data loss on round-trip |
| FR-08 | 非同步執行器 — `asyncio.TaskGroup` background executor | Async Executor / Concurrency | AC-driven verification (AC-8.1–AC-8.4) against `SPEC.md` §8 row 25 | DRAFT | Graceful drain with timeout; `CancelledError` must propagate |
| FR-09 | 健康檢查與可觀測性 — `/healthz`, `/readyz`, `/v1/metrics` | Observability / Health | AC-driven verification (AC-9.1–AC-9.4) against `SPEC.md` §8 rows 10/11 | DRAFT | `/readyz` fails closed when a migration has not run |
| FR-10 | 錯誤契約 — RFC 7807 `application/problem+json` | Error Contract | AC-driven verification (AC-10.1–AC-10.5) against `SPEC.md` §8 row 19 | DRAFT | `detail` must not leak SQL/stack/paths; `correlation_id` in header + logs |
| NFR-01 | 效能與查詢效率 — p95 latency, N+1 forbidden | Performance | Dimension: `performance` (mean-latency scoring only) | DRAFT | Gap: dimension doesn't read p95/AC-N1.1/AC-N1.2 thresholds — needs dedicated `pytest-benchmark` assertions |
| NFR-02 | HTTP 與資料層安全 — no `shell=True`/`eval`, no string-concat SQL, CORS deny-by-default | Security | Dimension: `security` (bandit) | DRAFT | Gap: bandit doesn't detect SQL string-concat (AC-N2.2) — needs the dedicated grep gate (`SPEC.md` §8 row 17) |
| NFR-03 | 錯誤處理、交易與非同步正確性 — no bare except, transaction rollback | Error Handling | Dimension: `error_handling` (file-level try/except anti-pattern scan) | DRAFT | Gap: dimension doesn't verify AC-N3.1/AC-N3.4/AC-N3.6 (transaction/retry semantics) — needs dedicated integration tests |
| NFR-04 | 敏感資料遮蔽 — redact secrets in stdout/stderr/logs | Security | Dimension: `security` (bandit) | DRAFT | Gap: bandit doesn't check the redaction regex or connection-string leakage — needs dedicated unit/integration test (`SPEC.md` §8 row 20) |
| NFR-05 | 文件覆蓋 — docstrings citing FR/NFR, OpenAPI summary/description | Documentation | Dimension: `documentation` (ast-docstrings, presence-only) | DRAFT | Gap: dimension doesn't check FR/NFR citation text or `/openapi.json` fields — needs dedicated test |
| NFR-06 | 架構分層契約 — `.importlinter` layering, no `sqlalchemy` outside repository | Architecture Constraints | Dimension: `architecture_constraints` (`lint-imports` exit code) | DRAFT | No gap identified per SRS coverage note |
| NFR-07 | 依賴與授權合規 — pinned deps, allowed licenses, SBOM | License Compliance | Dimension: `license_compliance` (`scancode` header scan) | DRAFT | Gap: dimension doesn't run the dependency-tree scan (`pip-licenses`) or check the SBOM artifact — needs dedicated task |
| NFR-08 | 變異測試 — mutmut score ≥ 70 on service/repository | Mutation Testing | Dimension: `mutation_testing` (mutmut protocol) | DRAFT | No gap identified per SRS coverage note |
| NFR-09 | 驗證真實性(零 skip 鐵律) — no skip/xfail, 0 skipped tests | Test Assertion Quality | Dimension: `test_assertion_quality` (ast-assertions, density only) | DRAFT | Gap: dimension doesn't read `pytest -q` skipped count, exclusion flags, or real-DB migration testing — needs dedicated CI checks |
| NFR-10 | 整合覆蓋 — ≥80% integration line coverage, ASGITransport | Integration Coverage | Dimension: `integration_coverage` (aggregate line coverage %) | DRAFT | Gap: dimension doesn't verify the required scenario checklist (AC-N10.3) — needs test-inventory tracking (e.g. `TEST_SPEC.md`) |
| NFR-11 | 可讀性 — MI ≥ 80, CC ≤ 10, file/handler size caps | Readability | Dimension: `readability` (`radon mi` average only) | DRAFT | Gap: dimension doesn't enforce per-function CC or per-file/handler size caps — needs dedicated lint checks |
| NFR-12 | 系統驗證目標 — `make verify-system` chains 4 steps, exits 0 | Execute Verification Target | Dimension: `execute_verification_target` (exit-code only) | DRAFT | Gap: dimension doesn't verify the 4 internal steps (AC-N12.1) — needs per-step assertions, not assumed from a green score |

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-09-07 | Initial creation — built FR-01..FR-10 / NFR-01..NFR-12 matrix from `01-requirements/SRS.md` | Agent A |
