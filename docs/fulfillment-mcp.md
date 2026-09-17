# Fulfillment MCP boundary

P1 adds a second independent MCP service for warehouse fulfillment status. It exposes one read-only
tool, `get_fulfillment_status(order_reference)`, on port `8002`. The existing logistics service
remains separate on port `8001`.

The service participates in cancellation as a deterministic precondition, not as an authority:

1. Tool Runtime validates the request, principal, permission, and MySQL order ownership first.
2. The fulfillment client requests the external status and validates a strict local schema.
3. Only `READY_TO_PICK` is cancellable. A mismatched reference, contradictory flag, malformed
   output, timeout, open circuit, or unavailable service fails closed before the local mutation.
4. `CommerceService.cancel_order` locks the MySQL row and revalidates the authoritative local order
   state immediately before changing it.

This placement avoids giving MCP output authorization power and avoids sending foreign order IDs to
the external service after an ownership denial. It also keeps the existing frozen evaluation tool
contract: the Agent still calls `cancel_order`; the Tool Runtime owns the external precondition.

The external read and MySQL write cannot form one distributed transaction. The implementation
minimizes that gap and deliberately does not claim atomic coordination with a real warehouse. A
production integration would require a provider-supported reservation/cancellation protocol with an
idempotency key, which is beyond this portfolio service.
