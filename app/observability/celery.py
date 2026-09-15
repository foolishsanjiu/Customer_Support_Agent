from typing import Any

from celery.signals import before_task_publish, task_postrun, task_prerun
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

CORRELATION_KEYS = (
    "request_id",
    "trace_id",
    "run_id",
    "ticket_id",
    "tool_call_id",
    "approval_id",
)
HEADER_NAME = "resolvex_correlation"


@before_task_publish.connect(weak=False)
def publish_correlation(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is None:
        return
    values = get_contextvars()
    correlation = {key: values[key] for key in CORRELATION_KEYS if key in values}
    if correlation:
        headers[HEADER_NAME] = correlation


@task_prerun.connect(weak=False)
def bind_task_correlation(task: Any = None, task_id: str | None = None, **_: Any) -> None:
    clear_contextvars()
    headers = getattr(getattr(task, "request", None), "headers", None) or {}
    correlation = headers.get(HEADER_NAME, {})
    safe = {key: correlation[key] for key in CORRELATION_KEYS if key in correlation}
    bind_contextvars(task_id=task_id, **safe)


@task_postrun.connect(weak=False)
def clear_task_correlation(**_: Any) -> None:
    clear_contextvars()
