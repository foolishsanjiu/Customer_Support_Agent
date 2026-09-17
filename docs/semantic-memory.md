# Semantic Memory

ResolveX stores only durable preferences that a customer explicitly states, such as language,
communication style, accessibility, or delivery preferences. Successful agent runs perform a
best-effort structured extraction. A memory failure is logged but cannot change an already
completed business result.

MySQL is the memory source of truth. Each record is scoped to one customer, tied to its source
ticket, deduplicated by normalized content hash, and stores its BGE-M3 embedding. Retrieval loads
only that customer's records and returns at most three matches within a cosine-distance threshold.
This bounded per-customer scan keeps the first implementation simple; a dedicated vector index is
only justified after measurements show it is needed.

The extraction prompt excludes inferred traits, contact and payment details, identifiers,
credentials, business state, eligibility, authorization, policy, promises, complaints, and
one-time requests. A deterministic filter also rejects email addresses, sensitive terms, and long
numeric identifiers before persistence.

Retrieved memories are labeled as untrusted historical preferences. They cannot grant
authorization, override policy, or replace current MySQL business state. Episodic memory and
cross-customer memory are intentionally out of scope.
