import json
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from app.evaluation.models import FunctionalCase, SecurityCase


def load_functional_cases(path: str | Path) -> list[FunctionalCase]:
    return _load_cases(path, FunctionalCase)


def load_security_cases(path: str | Path) -> list[SecurityCase]:
    return _load_cases(path, SecurityCase)


def _load_cases[CaseT: BaseModel](path: str | Path, model: type[CaseT]) -> list[CaseT]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = TypeAdapter(list[model]).validate_python(data)
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation case ids must be unique")
    return cases
