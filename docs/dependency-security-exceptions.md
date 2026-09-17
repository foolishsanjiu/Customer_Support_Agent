# Dependency security exceptions

Dependency-audit exceptions are narrow, temporary risk acceptances. They do not make the audit
advisory-only: every vulnerability not listed below still fails the production and development
lock-file jobs.

## ChromaDB 1.5.9

- Accepted advisories: `PYSEC-2026-311`, `PYSEC-2026-3813`, `PYSEC-2026-3814`, and
  `PYSEC-2026-3815`.
- Accepted on: 2026-09-17.
- Reason: ChromaDB 1.5.9 is the latest PyPI release and the advisories do not identify a fixed
  release. Downgrading below the affected ranges would cross multiple unsupported API generations
  and is not a credible security fix.
- Scope: ResolveX uses an in-process `PersistentClient` for its versioned policy corpus. It does not
  deploy or expose a Chroma server, accept Chroma credentials, provide tenant or collection
  administration, or pass user-supplied filter objects to Chroma. User text is limited to the
  similarity-query input.
- Compensating controls: the policy corpus and metadata are application-owned, the persistence path
  is local, and dependency audit continues weekly and on every relevant dependency change.

Remove these exceptions and upgrade ChromaDB as soon as a fixed release is available. Reassess them
immediately if ResolveX exposes a Chroma service, accepts untrusted filter or collection operations,
adds multi-tenant Chroma authorization, or ingests an untrusted policy corpus.
