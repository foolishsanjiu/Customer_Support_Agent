# Conversation Summary

ResolveX keeps the ticket subject and recent messages verbatim while incrementally summarizing
older `ticket_messages`. The rolling summary and the last summarized message ID are stored on the
ticket. A summary refresh processes at most 20 messages, so one long ticket cannot create an
unbounded model request.

The summary is historical context, not a source of truth. It is explicitly labeled as untrusted
data before intent classification and again in the Context Builder. Current order, shipment,
refund, customer, authorization, and policy decisions continue to come from deterministic
boundaries backed by MySQL and the Tool Runtime.

The refresh is cursor-based and conditionally persisted, preventing a slower concurrent refresh
from overwriting a newer cursor. The latest message window is never summarized. If generation or
persistence fails, ResolveX uses any safely persisted prior summary and includes every message
after its cursor verbatim. If no prior summary exists, it includes the full conversation. Summary
failure therefore increases token use but does not remove context or fail the AgentRun.

The current implementation refreshes inline only when a ticket exceeds the configured context
message limit. This avoids background infrastructure and unnecessary model calls for short
conversations. If measurements later show summary latency is material, the same cursor contract
can be moved to an asynchronous worker.
