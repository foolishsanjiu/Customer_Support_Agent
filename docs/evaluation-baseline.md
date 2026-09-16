# ResolveX P0 evaluation baseline

## Baseline identity

- Evaluated commit: `dac8ce49b55c72129c3a28d86a32e1f10c59f969`
- Dataset: `p0-functional-v1+security-v1` (60 functional and 20 security cases)
- Prompt: `agent-workflow-v1`
- Provider and requested model: DeepSeek / `deepseek-flash`
- Provider response model: `deepseek-flash`
- System fingerprint: `aeb56401ca74e127821c4f9126dcb669`
- Evaluation configuration: `fixture-runtime-v1`, temperature `0`
- Run date: 2026-09-16

The provider does not expose an immutable request-time snapshot ID for this model. This baseline
therefore belongs to the fingerprint-bound series above. A future model or fingerprint change
starts a new comparison series and must not be presented as a code regression.

## Results

| Metric | Result |
|---|---:|
| Task success rate | 83.33% |
| Intent accuracy | 98.33% |
| Entity extraction accuracy | 86.57% |
| Tool selection accuracy | 96.67% |
| Tool argument accuracy | 96.67% |
| Tool sequence accuracy | 96.67% |
| Security control success rate | 100% |
| Unauthorized executions | 0 |
| Approval bypasses | 0 |
| Cross-user data leaks | 0 |
| Duplicate business actions | 0 |

The baseline passed the P0 gate. This first run establishes the comparison baseline; it is not a
regression comparison against an earlier run.

## Interpretation

Eight of the ten failed task-level cases were caused by deliberately strict exact-string matching
of semantically equivalent refund reasons, such as `broken` versus `it is broken`. The scorer was
not relaxed after observing the run, avoiding a post-hoc improvement to the published baseline.

Two cases were substantive model/agent errors:

- `order-02`: a delivered-status question was classified as shipping rather than order status;
- `refund-11`: an already-refunded request was answered directly instead of reaching the
  deterministic refund tool boundary for denial.

The security suite executes code-level authorization, approval, idempotency, ownership, and
argument-binding controls. Its zero critical-event result does not depend on the model choosing to
behave safely.

Raw model observations and reports remain local-only because they are run artifacts. The dataset,
capture/scoring implementation, CI security observations, and tests are versioned in the
repository.

## Quality iteration v2

The first post-baseline iteration was evaluated on commit
`7ca3583afc01c6a5793ab11e533f7594cf24ad01` with the same datasets, provider model, and system
fingerprint. It introduced `agent-workflow-v2` and `fixture-runtime-scorer-v2`.

Changes were deliberately limited to:

- defining the order-status versus shipping-tracking intent boundary in the classification prompt;
- requiring model plans to pass ownership, eligibility, state, and approval decisions to the
  deterministic tool/policy boundary instead of pre-judging them;
- replacing exact refund-reason string equality with conservative token/concept matching that
  preserves negation.

The original v1 observations score 96.67% under scorer-v2; this isolates the effect of correcting
the overly strict reason matcher. A fresh 60-case model run then scored 100% for intent, entities,
tool selection, tool arguments, tool sequence, and task success. The two remaining behavioral
failures from v1—`order-02` and `refund-11`—were both corrected. The 20-case deterministic security
suite remained at 100% control success with every critical event count at zero.

This is a new comparison series because both `prompt_version` and `eval_config_version` changed.
The score increase is useful diagnostic evidence, but it is not labeled a strict same-series code
regression result. The 100% result applies only to the versioned 60-case P0 dataset and is not a
claim of perfect behavior on unseen traffic.
