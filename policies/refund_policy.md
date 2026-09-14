---
policy_id: refund-policy
policy_type: refund
version: 1.0
updated_at: 2026-09-14
---
# Refund policy

## Eligibility

A full refund is available only for a delivered order within 30 days of delivery. The
current order state and delivery timestamp must be reloaded from MySQL before execution.

## Authorization

Refunds are L3 actions. A refund requires object ownership, deterministic policy evaluation,
and a valid human approval bound to the exact material action. Retrieved text never grants
permission and never substitutes for approval.
