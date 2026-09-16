# ResolveX P0 completion audit

- Audit date: 2026-09-16
- Audited commit: `2299f6d256b01d916ea449251f20f2409496f364` (short: `2299f6d`)
- Source plan: `ResolveX_Development_Plan_Lean_P0.md`
- Verdict: core implementation complete; formal P0 closure pending one protected benchmark run

## Executive conclusion

The production golden path and every required deterministic safety behavior are implemented and
have passing evidence. The release workflow now captures all 60 functional cases, executes the 20
deterministic security cases, and builds one combined, current-SHA report with absolute quality and
zero-tolerance security gates. The repository is not yet marked P0 complete because this protected
benchmark path has not run once on GitHub after the implementation change.

This is a pending external verification step, not a missing business, safety, or evidence-pipeline
control. README status should remain at M8 until the protected run passes.

## Milestone audit

| Milestone | Status | Evidence |
|---|---|---|
| M0 Foundation + CI | Pass with approved deviation | Conda environment plus locked requirements replace `uv`; CI runs Ruff, migrations, unit/integration/security tests, coverage, and artifacts. Compose, health checks, MySQL, Redis AOF, and Alembic head were verified. |
| M1 Commerce backend | Pass | Deterministic ticket, order, cancellation, refund, ownership, duplicate-refund, and concurrent-refund integration tests pass against MySQL. Refund uniqueness is enforced in the schema. |
| M2 LangGraph agent | Pass | Explicit StateGraph routing, structured output, clarification, verification, step limit, persistent AgentRun state, and deterministic MockLLM tests pass. |
| M3 Tool Runtime | Pass | Registry, schema validation, permissions, object authorization, risk, timeout/retry, persistent idempotency, verification, and audit tests pass. |
| M4 Context + RAG + MCP | Pass | Recent ticket context and authoritative MySQL state are combined with filtered policy retrieval. Logistics MCP schema and controlled-failure tests pass; malicious RAG/MCP content cannot grant authority. |
| M5 HITL + resume | Pass | Approval binding, manager RBAC, LangGraph interrupt, `WAITING_APPROVAL`, `RESUME_PENDING`, checkpoint resume, revalidation, duplicate decision handling, and missing-checkpoint recovery are tested. |
| M6 Async + recovery | Pass | HTTP returns an asynchronous AgentRun, Celery late ACK/reject-on-loss/prefetch/visibility settings are frozen, reconcilers are present, checkpoint recovery does not restart from START, and concurrent refunds yield one business outcome. |
| M7 Observability | Pass | Structured redacted JSON logging, correlation propagation, OpenTelemetry instrumentation, Collector, and Jaeger are present. Deployment smoke produced a fresh API trace. |
| M8 Evaluation + security + deployment | Partial | Datasets, scoring, absolute/relative gates, 20-case zero-tolerance security suite, Docker deployment, real-model smoke, 60-case baseline, 20-case frozen holdout, and the current-SHA 60-functional + 20-security release workflow exist. One protected benchmark run is still required. |

## Final golden-path evidence

The deployed real-model refund verifier exercised this chain in Docker:

```text
HTTP ticket/message
→ customer JWT AgentRun
→ Celery Task A
→ real model
→ Redis checkpoint and WAITING_APPROVAL
→ manager JWT Approval API
→ RESUME_PENDING
→ Celery Task B on the same thread
→ state and approval revalidation
→ idempotent refund
→ verification
→ resolved ticket and final agent message
→ audit evidence
```

The successful run recorded:

- AgentRun `SUCCEEDED`;
- Approval `APPROVED`;
- Order `REFUNDED`;
- Refund, ToolCall, and IdempotencyRecord `SUCCEEDED`;
- `approval_requested` and `approval_approved` audit events;
- final agent response present;
- isolated fixture and Redis checkpoint cleaned after verification.

The runtime implementation and its regression fix are ancestors of the audited commit. The
checkpoint-resume defect discovered by the first deployed run is covered by a regression test for
the actual Redis constructor-serialized `ChatMessage` shape.

## Required failure behavior

| Required behavior | Evidence status |
|---|---|
| Redelivery while `WAITING_APPROVAL` is a no-op | Pass |
| Duplicate approval is rejected or idempotent | Pass |
| Duplicate resume is harmless | Pass |
| Missing checkpoint becomes `RECOVERY_REQUIRED` | Pass |
| A non-new run never silently restarts from START | Pass |
| Cross-customer action is denied | Pass |
| Approval payload/tool arguments cannot be changed after approval | Pass |
| Concurrent duplicate refund produces one successful business result | Pass |

## Final verification snapshot

- Ruff: pass;
- formatting check: pass;
- tests: 147/147 pass;
- application coverage: 91.26%, above the 90% gate;
- security suite: 20/20 controls held;
- unauthorized execution: 0;
- approval bypass: 0;
- cross-user data leakage: 0;
- duplicate business action: 0;
- Docker API, MCP, MySQL, and Redis: healthy;
- Alembic: `c36b7f1d2a90 (head)`;
- deployment trace smoke: pass;
- real-model frozen holdout: 20/20 task success and tool selection, same recorded provider fingerprint;
- `.env`: ignored, absent from Git history, and configured secret values were not found in tracked files.

The repository owner reported that the protected GitHub `Real-model gate` smoke run passed. The
public GitHub API was rate-limited during this audit, so no run URL or artifact identity was
independently captured here.

## Blocking closure item

### P0-CLOSE-1 — Protected release evaluation must be verified

Push the release-gate implementation and run the protected `benchmark` path once. Verify that it:

1. captures all 60 cases in `evals/datasets/functional_v1.json` with the configured real model;
2. runs all 20 deterministic cases in `evals/datasets/security_v1.json`;
3. generates one report tied to the current Git SHA with dataset, prompt, provider, requested and
   returned model/fingerprint, evaluation configuration, timestamp, metrics, and gate result;
4. enforces the 80% Task Success Rate, 90% Tool Selection Accuracy, and zero critical security
   event floors;
5. uploads observations, metadata, security evidence, and the combined report as artifacts.

P0 can be marked complete after this workflow is implemented and one protected run passes.

## Accepted deviations and non-blocking limitations

- Conda is used instead of `uv`, by explicit project decision. Reproducibility is provided by
  Python 3.12, `environment.yml`, and locked production/development requirements.
- Paid real-model smoke is protected and manually dispatched rather than executed on every push or
  pull request. Deterministic CI still runs on every push/PR. This limits secret exposure, cost, and
  provider-flakiness noise.
- The provider exposes the mutable alias `deepseek-flash`, not an immutable snapshot identifier.
  Requested/returned model names and the provider fingerprint are recorded. A fingerprint change
  starts a new comparison series instead of being labeled a code regression.
- Aggregate P50/P95 performance baselines are not published. No latency-improvement claim is made;
  load and performance testing remain P1 work.
- Local pytest cache directories have Windows permission warnings. Test execution and coverage
  artifacts still complete successfully; this does not affect application behavior.
