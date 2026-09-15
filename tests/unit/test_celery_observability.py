from types import SimpleNamespace

from structlog.contextvars import bound_contextvars, clear_contextvars, get_contextvars

from app.observability.celery import (
    HEADER_NAME,
    bind_task_correlation,
    clear_task_correlation,
    publish_correlation,
)


def test_celery_correlation_is_injected_and_restored() -> None:
    headers = {}
    with bound_contextvars(request_id="request-1", run_id=4, unrelated="ignored"):
        publish_correlation(headers=headers)

    assert headers == {
        HEADER_NAME: {"request_id": "request-1", "run_id": 4},
    }

    task = SimpleNamespace(request=SimpleNamespace(headers=headers))
    bind_task_correlation(task=task, task_id="task-1")
    assert get_contextvars() == {
        "request_id": "request-1",
        "run_id": 4,
        "task_id": "task-1",
    }
    clear_task_correlation()
    assert get_contextvars() == {}


def test_celery_correlation_handles_missing_headers() -> None:
    clear_contextvars()
    publish_correlation(headers=None)
    publish_correlation(headers={})
    bind_task_correlation(task=SimpleNamespace(request=SimpleNamespace(headers=None)))
    assert get_contextvars() == {"task_id": None}
    clear_task_correlation()
