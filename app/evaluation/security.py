from collections.abc import Awaitable, Callable, Iterable, Mapping

from app.evaluation.models import SecurityCase, SecurityObservation

SecurityCheck = Callable[[SecurityCase], Awaitable[SecurityObservation]]


async def run_security_checks(
    cases: Iterable[SecurityCase], checks: Mapping[str, SecurityCheck]
) -> list[SecurityObservation]:
    case_list = list(cases)
    case_ids = {case.id for case in case_list}
    check_ids = set(checks)
    if case_ids != check_ids:
        raise ValueError(
            f"security checks do not match dataset; missing={sorted(case_ids - check_ids)}, "
            f"unexpected={sorted(check_ids - case_ids)}"
        )

    observations: list[SecurityObservation] = []
    for case in case_list:
        observation = await checks[case.id](case)
        if observation.case_id != case.id:
            raise ValueError(
                f"security check {case.id} returned observation for {observation.case_id}"
            )
        observations.append(observation)
    return observations
