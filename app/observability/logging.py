import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace

SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "card_number",
    "cvv",
    "password",
    "secret",
    "token",
)


def redact_sensitive(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return _redact_mapping(event_dict)


def add_trace_context(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict.setdefault("trace_id", format(context.trace_id, "032x"))
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(service: str, level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    timestamp = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        timestamp,
        add_trace_context,
        _service_processor(service),
        redact_sensitive,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


def _service_processor(service: str):
    def add_service(_logger: Any, _method_name: str, event_dict: dict[str, Any]):
        event_dict["service"] = service
        return event_dict

    return add_service


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "[REDACTED]" if _is_sensitive(key) else _redact_value(item)
        for key, item in value.items()
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)
