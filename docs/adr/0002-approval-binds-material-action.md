# ADR 0002: Approval binds the material action

- Status: Accepted
- Date: 2026-09-14
- Scope: Lean P0

## Context

An approval must not authorize a different order, amount, or action after resume. At the same time, revalidation must use current business state rather than blindly executing a stale snapshot.

## Decision

1. Approval preparation stores the canonical JSON form of all material action fields and a SHA-256 fingerprint. For refund, material fields are `tool_name`, `order_id`, `amount`, `currency`, `reason`, `customer_id`, `run_id`, and `tool_call_id`.
2. Canonicalization uses stable field names, UTF-8, sorted keys, and normalized decimal/currency representations. Trace IDs, timestamps, and presentation-only text are non-material.
3. An approval is valid only for the same run, tool call, tool, principal scope, and material-action fingerprint. It is single-use.
4. Resume always reloads current MySQL state and reruns authorization, business rules, and policy rules.
5. If current state makes the approved action invalid, execution is denied and audited. If any material action field must change, the old approval becomes `CANCELLED` or `EXPIRED`, a new tool call and approval are created, and execution pauses again.
6. Revalidation may narrow the operation only when it results in no execution; it may never silently change and execute an approved action.

## Consequences

- Approval cannot be replayed or stretched to cover a changed refund.
- Current business truth remains authoritative.
- A changed amount or target incurs another human decision.

## Verification

- Tests cover amount, order, customer, currency, reason, and tool tampering.
- Tests prove non-material metadata changes do not invalidate an approval.
- Tests prove a changed material field creates no business mutation before reapproval.

