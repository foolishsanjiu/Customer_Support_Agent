---
policy_id: cancellation-policy
policy_type: cancellation
version: 1.0
updated_at: 2026-09-14
---
# Cancellation policy

## Eligibility

Orders may be cancelled only while their current MySQL status is CREATED or PAID. Shipped,
delivered, cancelled, refund-pending, and refunded orders cannot be cancelled.

## Source of truth

Conversation history can describe an earlier order state, but it is not authoritative. The
Tool Runtime must authorize ownership and reload the order in the write transaction.
