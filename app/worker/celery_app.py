from celery import Celery

from app.core.config import get_settings

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
