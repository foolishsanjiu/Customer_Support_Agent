# ADR 0001: Refund execution boundary

- Status: Accepted
- Date: 2026-09-14
- Scope: Lean P0

## Context

Refund is an L3 high-risk action. A public business endpoint that can call the refund service directly would bypass the Agent Tool Runtime, policy decision, and human approval path.

## Decision

1. `POST /api/v1/agent-runs` is the public entry point for a customer refund request.
2. `POST /api/v1/orders/{order_id}/refund` is not exposed in the deployed P0 API. M1 exercises refund behavior through service and integration tests instead.
3. The only runtime caller allowed to execute a refund is the registered `refund_order` Tool Runtime handler.
4. The handler may call `RefundService` only after schema validation, principal and object authorization, policy evaluation, and—when required—a valid approval bound to the current tool call.
5. `RefundService` remains the final deterministic guard. Inside one MySQL transaction it re-reads ownership and order state, validates eligibility and amount, applies the unique constraint on `refunds.order_id`, and updates the order and refund records.
6. No role, including administrator, receives an undocumented bypass. Any future break-glass path requires a separate ADR and audit design.

## Consequences

- A user cannot bypass HITL by calling a lower-level API.
- M1 can prove business correctness without prematurely exposing a destructive route.
- Tool Runtime and service-level checks provide defense in depth.

## Verification

- A route inventory test proves the direct refund-execution endpoint is absent.
- Tests prove direct service execution without an approved execution context is rejected.
- Security tests cover customer, support-agent, and forged-approval bypass attempts.

