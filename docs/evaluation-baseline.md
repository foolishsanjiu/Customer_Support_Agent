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
