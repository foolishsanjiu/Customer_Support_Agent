import json
from pathlib import Path

from app.evaluation.models import EvalReport, GateResult


def write_evaluation_report(path: str | Path, report: EvalReport, gate: GateResult) -> None:
    output = Path(path)
    payload = report.model_dump(mode="json")
    payload["gate"] = gate.model_dump(mode="json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
