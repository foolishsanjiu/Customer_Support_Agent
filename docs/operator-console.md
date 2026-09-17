# Small operator console

ResolveX serves a dependency-free operator console at `http://localhost:8000/operator`. It is a
small operational surface for the P1 stage, not a replacement for a full case-management frontend.

## Access

Paste a short-lived JWT into the page and select **Connect**. The token stays only in the page's
JavaScript memory; it is not written to cookies, local storage, or session storage. Closing,
refreshing, or disconnecting the page removes it.

The API remains the authorization boundary:

- `SUPPORT_AGENT`, `MANAGER`, and `ADMIN` can inspect recent AgentRun status;
- `MANAGER` and `ADMIN` can inspect and decide pending approvals;
- only `ADMIN` can inspect and request replay of open dead letters.

The role decoded in the browser is used only to hide irrelevant panels. Every request is validated
again by the authenticated FastAPI endpoint.

## Deliberate limits

The console displays operational metadata only. Approval argument snapshots, customer messages,
customer profiles, and tool payloads are excluded to reduce accidental disclosure. It refreshes
every 15 seconds while connected and uses the existing approval and DLQ APIs; replay still preserves
the original AgentRun and idempotency keys.

Run filtering and single-item actions are included. Search, pagination, bulk replay, case editing,
and a full customer-service workspace remain outside this small P1 console.
