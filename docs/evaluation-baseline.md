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

## Frozen holdout v1

To test whether the 60/60 result generalized beyond the prompt-tuning set, 20 new cases were
written, validated, hashed, and committed before the model was allowed to run them:

- frozen commit: `f9924f22e538bb279eac4d4b7e245da8b55d6c62`;
- canonical dataset SHA-256:
  `5cfe7273ef9dfe0528e95bca6b8487190a884905573ddb11e4dca84cb7f944a5`;
- model fingerprint: `aeb56401ca74e127821c4f9126dcb669`;
- prompt/configuration: `agent-workflow-v2` / `fixture-runtime-scorer-v2`.

The untouched first run produced:

| Holdout metric | Result |
|---|---:|
| Task success rate | 90% (18/20) |
| Intent accuracy | 100% |
| Entity extraction accuracy | 95.65% |
| Tool selection accuracy | 90% |
| Tool argument accuracy | 100% |
| Tool sequence accuracy | 90% |
| Security control success rate | 100% |
| Critical security events | 0 |

The two failures reveal an unresolved definition problem around a required refund reason:

- `h-refund-03`: “another refund” was treated as missing a reason, so the agent asked for
  clarification instead of reaching the deterministic already-refunded denial;
- `h-missing-02`: “I need my money back” was treated as a reason, so the agent attempted the
  refund path instead of asking why the refund was requested.

The frozen dataset was not edited and the model was not rerun to replace these results. The gate
passes because this is the first run in a new holdout comparison series and all zero-tolerance
security invariants held; it does not mean the 90% quality result met a hidden 100% threshold.

## Refund preflight iteration v3

The two holdout failures were addressed on commit
`a88e589bf2e21c7a7549f8b8fd02842e460d9fc0` without changing the frozen dataset. The iteration
made the refund contract explicit in two places:

- the classifier now treats damage, a wrong item, or another causal explanation as a refund
  reason; merely asking for money back is not a reason;
- before asking for a reason, the workflow uses authoritative business context to reject an order
  that belongs to another customer, has already been refunded, is not delivered, or is outside
  the 30-day refund window. Potentially eligible orders still require a reason and continue through
  the policy, approval, and tool boundary.

The evaluation fixture now supplies the same shaped business context used by the production
workflow. Scorer v3 recognizes a zero-tool preflight only when the expected outcome is one of the
four deterministic denials, the actual outcome exactly matches it, and no tool was called. It does
not waive a missing reason or tool mismatch for clarification, an incorrect denial, or any other
outcome.

A fresh run against the same 20-case frozen holdout produced:

| Holdout metric | Result |
|---|---:|
| Task success rate | 100% (20/20) |
| Intent accuracy | 100% |
| Entity extraction accuracy | 100% |
| Tool selection accuracy | 100% |
| Tool argument accuracy | 100% |
| Tool sequence accuracy | 100% |
| Security control success rate | 100% (20/20) |
| Critical security events | 0 |

The canonical dataset SHA-256 remained
`5cfe7273ef9dfe0528e95bca6b8487190a884905573ddb11e4dca84cb7f944a5`, and the provider returned
the same system fingerprint, `aeb56401ca74e127821c4f9126dcb669`. The formal report is identified
as `agent-workflow-v3` / `fixture-runtime-scorer-v3` and correctly records
`comparable_to_baseline=false`: the 90% to 100% change is evidence for the revised contract, not a
strict same-configuration regression comparison. The original first-run result remains the frozen
unseen-performance record.

## Absolute quality gate v1

Commit `a4954813646fbf018f0d6b0d171801fe223f6998` adds functional quality floors that apply even
when a report has no baseline or belongs to a new, non-comparable series:

- Task Success Rate must be at least 80%;
- Tool Selection Accuracy must be at least 90%.

These defaults preserve both published P0 results—the original 60-case baseline scored 83.33% and
96.67%, while the frozen holdout first run scored 90% for both metrics—but prevent a materially
weaker first run from passing merely because relative comparison is unavailable. Same-series
reports must satisfy these floors and the existing two-percentage-point regression tolerance.
Thresholds outside the range from zero to one are rejected, and every combined report stores the
effective thresholds in `gate.absolute_quality_thresholds`.

The deterministic CI security report contains no functional observations, so it explicitly runs
the gate in security-only mode. This is not the default and does not affect combined reports;
unauthorized execution, approval bypass, cross-user leakage, and duplicate business actions remain
zero-tolerance failures.

The previous v3 holdout observations were rescored without another model call to isolate this gate
change. The resulting report, identified as
`fixture-runtime-scorer-v3+absolute-gate-v1`, passed with 100% Task Success Rate, 100% Tool
Selection Accuracy, and zero critical security events. It records
`comparable_to_baseline=false` because the evaluation configuration changed; this validation is a
gate check, not a new claim about model performance.

## Release-candidate holdout verification

The complete 20-case frozen holdout was captured again on commit
`24cf0b8d94f3795259311697cab4f6dfe7de0d9a` after the real-model CI/release gate was added. The
canonical dataset SHA-256 remained
`5cfe7273ef9dfe0528e95bca6b8487190a884905573ddb11e4dca84cb7f944a5`. The requested and returned
model were both `deepseek-flash`, and the provider fingerprint remained
`aeb56401ca74e127821c4f9126dcb669`.

| Verification metric | Result |
|---|---:|
| Cases captured | 20/20 |
| Task success rate | 100% |
| Intent accuracy | 100% |
| Entity extraction accuracy | 100% |
| Tool selection accuracy | 100% |
| Tool argument accuracy | 100% |
| Tool sequence accuracy | 100% |
| Average agent steps | 8.45 |
| Average input tokens | 788.8 |
| Average output tokens | 338.65 |

The absolute release gate passed its 80% Task Success Rate and 90% Tool Selection Accuracy floors.
The dataset, prompt contract, scorer contract, requested and returned model, and provider
fingerprint match the existing v3 holdout series, so this run is evidence that the current release
candidate did not regress that series. Raw observations and metadata remain local-only under the
ignored `evals/observations/` directory.
