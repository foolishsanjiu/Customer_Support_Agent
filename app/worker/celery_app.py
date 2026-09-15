from celery import Celery
from celery.signals import beat_init, setup_logging, worker_process_init

from app.core.config import get_settings

# Importing the module registers correlation propagation signal handlers.
from app.observability import celery as _correlation  # noqa: F401
from app.observability.logging import configure_logging
from app.observability.tracing import instrument_celery

settings = get_settings()
celery_app = Celery("resolvex", broker=settings.celery_broker_url)
celery_app.conf.update(
    imports=("app.worker.tasks",),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=3600,
    broker_transport_options={"visibility_timeout": 3900},
    beat_schedule={
        "reconcile-resume-pending": {
            "task": "resolvex.reconcile_resume_pending",
            "schedule": 30.0,
        },
        "reconcile-pending-runs": {
            "task": "resolvex.reconcile_pending_runs",
            "schedule": 30.0,
        },
    },
)


@setup_logging.connect(weak=False)
def configure_celery_logging(**_) -> None:
    configure_logging(settings.service_name, settings.log_level)


def _configure_celery_tracing() -> None:
    instrument_celery(
        service=settings.service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        enabled=settings.otel_enabled,
    )


@worker_process_init.connect(weak=False)
def configure_worker_tracing(**_) -> None:
    _configure_celery_tracing()


@beat_init.connect(weak=False)
def configure_beat_tracing(**_) -> None:
    _configure_celery_tracing()
