# Workspace, customer portal, and CSR receiver

The application now serves `/workspace` for staff and `/portal?thread=<uuid>`
for customers. Both use the existing bearer JWT contract. Tokens stay in tab
memory. Customer endpoints enforce conversation ownership; `chat:manage` grants
staff access, `knowledge:manage` controls source releases, and `intake:manage`
allows upload-service progress callbacks. Production still requires JWT auth.

## Database upgrade

Run `uv run --frozen alembic upgrade head` before starting either service.
PostgreSQL is required for production booking exclusion and process-independent
mutation locks. SQLite supports development tests only. Revision `20261010_0002`
requires permission to install PostgreSQL's `btree_gist` extension. It prevents
overlapping active appointments for the same CSR; adjacent slots are allowed.
Existing overlapping rows must be resolved before this migration can succeed.
Find them before upgrading:

```sql
SELECT a.id, b.id, a.csr_id
FROM sales_consultations a JOIN sales_consultations b
  ON a.csr_id = b.csr_id AND a.id < b.id
 AND a.starts_at_utc < b.ends_at_utc AND b.starts_at_utc < a.ends_at_utc
WHERE a.status = 'scheduled' AND b.status = 'scheduled'
  AND a.cancelled_at IS NULL AND b.cancelled_at IS NULL;
```

Revision `20261010_0004` converts legacy consultation customer, lead and thread
identifiers from strings to UUID columns, matching the application model.
Preflight these values before upgrading (PostgreSQL 16 or newer):

```sql
SELECT id, customer_id, lead_id, thread_id FROM sales_consultations
WHERE NOT pg_input_is_valid(customer_id, 'uuid')
   OR NOT pg_input_is_valid(lead_id, 'uuid')
   OR NOT pg_input_is_valid(thread_id, 'uuid');
```

Resolve invalid identifiers against their original customer, lead or thread.
The migration fails atomically on invalid values; it never drops associations.

CI exercises migrated PostgreSQL, concurrent overlapping inserts, cancellation
and adjacent slots, then downgrades and reapplies the new revisions. Downgrading
removes the new workflow and receiver tables; it is a disposable CI check, not
a production rollback procedure.

## Companion receiver

The receiver lives in `bookcraft.receiver`, in the same repository and image.
It can run as a separate service, using its own database or the backend database.
Apply the migrations to whichever database it uses, then run:

```sh
uvicorn bookcraft.receiver.main:app --host 0.0.0.0 --port 8000
```

Set `CSR_NODE_API_URL` on the chat backend to the receiver's private base URL.
Set the same random `CSR_NODE_API_TOKEN` of at least 32 characters on both
services. Use TLS between hosts. The legacy setting names are retained for
configuration compatibility; a separate Node application is no longer needed.

`POST /api/consultations` requires the service bearer token and an
`Idempotency-Key`. An outbox event keeps the same key on every retry. The receiver
atomically records that key and the appointment version, rejects changed payloads
under a reused key, ignores older versions, and acknowledges the effective ID
and version. Cancellation is a newer version of the same appointment. A durable
thread tombstone rejects deliveries after deletion. API and receiver contract
tests run together using an in-process HTTP transport with synthetic data.

The customer portal lists availability in the customer's IANA timezone and
supports booking, rescheduling, cancellation, and reminder consent. Updates
require `expected_version`; a 409 means refresh availability or the appointment.
Booking state and the outgoing event commit in one database transaction.
External cancellation callbacks also update the local appointment and outbox
atomically. Include `expected_version` after a reschedule: a legacy callback
without it cannot cancel an appointment newer than version 1.

The delivery worker validates acknowledgments, retries transient failures with
backoff, and quarantines permanent failures or exhausted attempts. Staff can
inspect and replay quarantined events at `/workspace`; replay preserves identity.
Monitor `csr_outbox_deliveries_total` and `csr_outbox_oldest_pending_seconds`.

## Opt-in reminders

Configure `REMINDER_SMTP_HOST`, port, sender, and optional credentials on the
receiver. SMTP uses STARTTLS with certificate verification. Only customers who
opted in receive a reminder, during the 24 hours before their appointment.
Cancelled or deleted appointments are excluded. Each appointment version has
one reminder record and a stable email Message-ID.

An SMTP timeout can occur after mail was accepted, so it is unsafe to retry
automatically. The receiver records `needs_reconciliation` before sending, then
marks successful delivery `sent`. Inspect `csr_reminders` and the SMTP provider's
delivery log when reconciliation is needed. The service never retries an
ambiguous send automatically. Configuration alone does not prove deliverability;
verify it with a consenting test recipient in the deployment environment.

## Knowledge releases

Staff import a complete JSON release containing `title`, a future timezone-aware
`expires_at`, and `chunks` following the existing `RagChunk` schema. Every chunk
needs a unique ID, SHA-256 content checksum, source ID, title, section, filename,
and content version. Existing restrictions on unapproved prices and timelines
remain enforced. Supplied vectors are discarded.

The workflow is draft, preview, approval, publication. Evidence preview shows
matching passages and citations. Set `generate_answer: true` on the preview API
to call the configured response provider against those passages in isolated
state, without customer mutations or tools. It also returns the quality report;
the result is a staff preview, not a guarantee of live retrieval performance.
Approved content is immutable. Publication embeds with the configured model,
builds a fresh index, persists its identity, then atomically switches the alias.
Retrying alias activation never rewrites a live index. Rollback reactivates an
unexpired published release. Retrieval excludes expired chunks. An interrupted
index build can leave an unreferenced index; remove it only after checking the
active alias and stored workflow index references.

## Manuscript progress and feedback

The intake API tracks metadata through `awaiting_upload`, `uploaded`, `scanning`,
`extracting`, `awaiting_assessment`, `assessing`, and `completed`. Failures carry
bounded reason codes; an unsafe file cannot be retried. Callbacks require the
current version and a legal transition. The customer portal displays progress
and offers eligible retries. The existing upload/assessment service owns file
bytes and actual scanning/extraction; the chat backend does not claim to have
read a manuscript from metadata alone.

Feedback is scoped to a real conversation turn, can be updated by its owner,
and redacts contact information from optional comments. Staff analytics show
conversation, lead, booking, and feedback counts. Conversion uses bookings from
the same conversation creation cohort as its denominator.

## Export, deletion, and retention

Export uses bounded message pages with an `after_sequence` cursor. A deletion
request immediately hides the conversation and creates a retryable cleanup job.
It removes conversation records, generated documents, cache entries, traces,
and receiver appointments/receipts/reminders. The minimal ownership tombstone
remains so delayed writes cannot restore erased data and owners can check status.
This is conversation erasure, not deletion of a shared customer account.

If original manuscript files live in another service, configure
`ATTACHMENT_CLEANUP_URL` and `ATTACHMENT_CLEANUP_TOKEN`. That service must support
idempotent authenticated `DELETE /api/threads/{uuid}/data`, acknowledge
`{"status":"deleted"}`, and reject delayed uploads for the deleted thread.
Cleanup stays queued until this acknowledgment succeeds. Backup expiration,
SMTP-provider records, and copies exported by customers remain governed by the
operators of those systems.

Inactive conversations expire after `CONVERSATION_RETENTION_DAYS` (default 365).
Traces expire after `TRACE_RETENTION_DAYS` (default 30). Trace queries now use
SQLite WAL indexes and bounded keyset pagination rather than scanning JSONL.
Legacy JSONL is imported once under a database writer lock and removed only
after commit. Stop old versions before upgrading; do not run a JSONL-writing
version concurrently. Use a persistent local trace volume per backend instance;
SQLite is not a multi-host shared database. Apply deletion/retention on every
instance volume and govern any centralized log exports separately.

## Performance and validation

Embeddings reuse an HTTP connection pool and collapse simultaneous identical
requests. Cache identity includes provider URL, model revision, dimensions,
language, and preprocessing revision. Set `EMBEDDING_MODEL_REVISION` whenever a
TEI model changes. Invalid dimensions and non-finite vectors are rejected.
Intent-policy helpers have been extracted from chat orchestration. Traces retain
draft quality results and now separately report final-response quality.

Use `scripts/perf/capture_latency_baseline.py` and
`scripts/perf/benchmark_retrieval.py` for deployment-specific latency and recall
comparisons. Event batching, extraction overlap, background graph persistence,
and ANN remain opt-in. Do not enable them on the strength of synthetic timings.
Monitor stage latency (including `lock_wait`), provider usage/cost, tool outcomes,
cache activity, and outbox age alongside user feedback.

Image CI builds and runs both services as non-root, produces an SPDX SBOM, scans
OS and application dependencies, blocks fixable high/critical vulnerabilities,
and exports the image with checksums. Non-PR runs attest provenance and the SBOM.
There is no deployment or registry publication. Deployment infrastructure,
production secrets, real email delivery, and live model/Elasticsearch performance
remain environment-specific acceptance checks.

Regression expectations retain the established welcome-first, answer-before-
capture, consultation-form, one-time contact-enrichment, and warning-then-block
policies. New fixes preserve an explicit contact refusal and prioritize ambiguous
project clarification. Canonical manuscript status is `completed`. Tests now
check final output separately from rejected drafts and treat whitespace as 422.
