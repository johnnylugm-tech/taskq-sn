"""[FR-01] Failing acceptance tests for the /v1/tasks CRUD API.

RED step: taskq_api does not exist yet, so this module is expected to fail
collection with ModuleNotFoundError until FR-01 is implemented per
02-architecture/SAB.json's declared module paths.
"""
import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as SqlaSession

import taskq_api.app as app_module
from taskq_api.api import dependencies as api_dependencies
from taskq_api.api import schemas  # noqa: F401  SAB FR-01 module, exercised via HTTP below
from taskq_api.service import tasks as tasks_service  # noqa: F401  SAB FR-01 module
from taskq_api.repository import tasks_repo  # noqa: F401  SAB FR-01 module
from taskq_api.repository import session as db_session
from taskq_api.models import task as task_model  # noqa: F401  SAB FR-01 module
from taskq_api.models import task_result as task_result_model


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{tmp_path / 'fr01_test.db'}")
    monkeypatch.setenv("TASKQ_CORS_ORIGINS", "")
    importlib.reload(app_module)

    # GREEN TODO: taskq_api.api.dependencies must expose three module-level
    # FastAPI dependency callables usable directly as Depends(require_write)
    # (no factory call): require_read, require_write, require_admin. This is
    # the "single auth+scope+rate-limit dependency" SAD.md describes, owned
    # by FR-03/FR-04/FR-05 — not FR-01. None of FR-01's 17 declared cases
    # exercise 401/403 (those are FR-03/FR-04's own cases), so every FR-01
    # test overrides all three to bypass auth/scope and isolate CRUD
    # behavior. This is test isolation, not an FR-01 implementation.
    app_module.app.dependency_overrides[api_dependencies.require_read] = (
        lambda: {"scope": "read", "key_id": "test-read"}
    )
    app_module.app.dependency_overrides[api_dependencies.require_write] = (
        lambda: {"scope": "write", "key_id": "test-write"}
    )
    app_module.app.dependency_overrides[api_dependencies.require_admin] = (
        lambda: {"scope": "admin", "key_id": "test-admin"}
    )

    with TestClient(app_module.app) as test_client:
        yield test_client

    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def db_engine(client):
    # GREEN TODO: taskq_api.repository.session must expose get_engine(),
    # returning the SQLAlchemy Engine bound to TASKQ_DB_URL, so tests can
    # seed/verify rows through the same engine the app uses.
    return db_session.get_engine()


def _create_task(client, name, command="echo hello"):
    return client.post("/v1/tasks", json={"name": name, "command": command})


def _seed_task_result(db_engine, task_id):
    with SqlaSession(db_engine) as session:
        session.add(
            task_result_model.TaskResult(
                task_id=task_id,
                exit_code=0,
                stdout_tail="ok",
                stderr_tail="",
                duration_ms=5,
                finished_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _count_task_results(db_engine, task_id):
    with SqlaSession(db_engine) as session:
        return (
            session.query(task_result_model.TaskResult)
            .filter_by(task_id=task_id)
            .count()
        )


def test_fr01_create_task_returns_201(client):
    response = _create_task(client, name="build-report", command="echo hello")
    assert response.status_code == 201
    body = response.json()
    assert "id" in body


def test_fr01_get_task_returns_full_record(client):
    created = _create_task(client, name="build-report-get", command="echo hello").json()

    response = client.get(f"/v1/tasks/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["name"] == "build-report-get"
    assert body["command"] == "echo hello"
    assert "status" in body


def test_fr01_list_tasks_returns_200_with_items(client):
    for i in range(3):
        _create_task(client, name=f"list-task-{i}")

    response = client.get("/v1/tasks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3


def test_fr01_delete_task_cascades_result_rows_in_same_transaction(client, db_engine):
    created = _create_task(client, name="task-with-results").json()
    task_id = created["id"]
    _seed_task_result(db_engine, task_id)
    assert _count_task_results(db_engine, task_id) == 1

    response = client.delete(f"/v1/tasks/{task_id}")

    assert response.status_code in (200, 204)
    assert client.get(f"/v1/tasks/{task_id}").status_code == 404
    assert _count_task_results(db_engine, task_id) == 0


def test_fr01_create_task_empty_command_rejected(client):
    response = client.post("/v1/tasks", json={"name": "empty-command-task", "command": ""})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_create_task_injection_char_rejected(client):
    response = client.post(
        "/v1/tasks", json={"name": "injection-task", "command": "echo hi; rm -rf /tmp"}
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_get_task_unknown_id_returns_404(client):
    response = client.get("/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_create_task_duplicate_name_returns_409(client):
    first = _create_task(client, name="dup-task", command="echo hi")
    assert first.status_code == 201

    second = _create_task(client, name="dup-task", command="echo hi")

    assert second.status_code == 409
    assert second.headers["content-type"].startswith("application/problem+json")


def test_fr01_create_task_name_at_1000_chars_accepted(client):
    response = _create_task(client, name="a" * 1000, command="echo hi")
    assert response.status_code == 201


def test_fr01_create_task_name_above_1000_chars_rejected(client):
    response = _create_task(client, name="a" * 1001, command="echo hi")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_create_task_empty_name_rejected(client):
    response = _create_task(client, name="", command="echo hi")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_list_tasks_default_limit_is_50(client):
    for i in range(60):
        _create_task(client, name=f"bulk-task-{i}")

    response = client.get("/v1/tasks")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 50


def test_fr01_list_tasks_limit_at_200_accepted(client):
    response = client.get("/v1/tasks", params={"limit": 200})
    assert response.status_code == 200


def test_fr01_list_tasks_limit_above_200_rejected(client):
    response = client.get("/v1/tasks", params={"limit": 201})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_fr01_list_tasks_cursor_pagination_returns_next_page(client):
    for i in range(5):
        _create_task(client, name=f"page-task-{i}")

    first_page = client.get("/v1/tasks", params={"limit": 2}).json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"]

    second_page = client.get(
        "/v1/tasks", params={"limit": 2, "cursor": first_page["next_cursor"]}
    ).json()

    assert len(second_page["items"]) == 2
    first_ids = {item["id"] for item in first_page["items"]}
    second_ids = {item["id"] for item in second_page["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_fr01_list_must_not_use_offset_query_param(client):
    schema = client.get("/openapi.json").json()
    list_params = schema["paths"]["/v1/tasks"]["get"]["parameters"]
    param_names = {param["name"] for param in list_params}

    assert "cursor" in param_names
    assert "limit" in param_names
    assert "status" in param_names
    assert "offset" not in param_names


def test_fr01_create_task_output_feeds_fr02_run_pipeline(client):
    created = _create_task(client, name="feed-task", command="echo hi").json()

    assert "id" in created
    assert created["status"] == "pending"

    fetched = client.get(f"/v1/tasks/{created['id']}").json()
    assert fetched["id"] == created["id"]
    assert fetched["command"] == "echo hi"
