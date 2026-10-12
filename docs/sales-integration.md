# ERP Sales integration

The chatbot can use the Sales module as its customer, lead, policy, and appointment
system of record. Set `SALES_INTEGRATION_MODE=erp_mcp` to enable this path.
The standalone receiver remains available under `standalone`. Never point
`CSR_NODE_API_URL` at ERP Sales: those services implement different protocols.

## Deployment configuration

Configure `API_AUTH_MODE=jwt`, a strong `JWT_SIGNING_KEY`, the chatbot PostgreSQL
database, and:

| Setting | Required value |
| --- | --- |
| `SALES_MCP_URL` | Full HTTPS URL ending in `/mcp`; loopback HTTP is allowed for tests |
| `SALES_MCP_INTERNAL_SECRET` | Exact Sales `MCP_INTERNAL_SECRET` |
| `SALES_SERVICE_JWT_FILE` | Read-only file containing a current ERP-issued EdDSA service JWT |
| `SALES_TENANT_ID` | One verified Sales tenant UUID for this chatbot instance |
| `SALES_ACTOR_ID` | Stable ERP service user ID; keep unchanged across retries |
| `SALES_TIMEOUT_SECONDS` | Total request budget, default 30 seconds |

The ERP credential issuer/sidecar must rotate the file atomically before expiration.
Tokens use `kid`, issuer `erp-rbac` or `erp-gateway`, audience `internal`,
the configured tenant and user, and these exact scopes:

```text
sales.customer.read sales.customer.update sales.lead.create
sales.lead.read sales.lead.update sales.config.read
```

Register the issuer public key in Sales' `SERVICE_JWT_PUBLIC_KEYS_JSON`.
The chatbot never mints its own privileged identity. Its local token checks
prevent tenant/actor drift; Sales verifies the signature and authorizes each tool.
Administrative policy, assignments, completion, and privacy approval scopes are
not required. The MCP client sends `X-Service-Authorization` and `X-MCP-Internal`,
uses a shared HTTP connection pool, disallows redirects, and bounds time and
response size. The official Python `mcp` SDK owns the Streamable HTTP transport,
initialization, protocol negotiation, tool discovery, and `call_tool` requests.
There is no handwritten JSON-RPC request/response implementation or gRPC bridge.

The SDK uses handshake mode for Sales' `2025-06-18` protocol. Each call gets an
isolated MCP session with a fresh read of the rotated service token, while HTTP
connections are reused. The allowlisted tool must appear in `tools/list` before
execution (discovery pagination is bounded). JSON and SSE responses are supported;
structured results or Sales' JSON text content are decoded at the domain boundary.
The complete handshake/discovery/call shares the configured timeout. Responses
are limited to 1 MiB and request uncompressed transfer. Upstream error text and
SDK payload logs are redacted. Existing Sales domain statuses remain 404/409/422,
including Sales' `-32000` errors, which overlap an SDK transport error code.

No failed mutation is automatically reissued with a new operation ID. An uncertain
outcome remains in the durable workflow journal for an explicit retry. The Sales
router acknowledges MCP notifications with an empty HTTP 202 and supports `ping`.

Set `READINESS_CHECK_EXTERNALS=true` to include authenticated Sales access in
readiness checks. The check validates all six credential scopes, discovers all
11 required tools, and reads the consultation policy; it performs no business
writes. Failures return a safe dependency error without credentials.
Configure enabled consultation policy, eligible CSR profiles/skills and the
corresponding RBAC ownership in Sales before offering appointments.

This release builds and validates images. Deployment and credential provisioning
remain separate operations.

## Website and customer identity

Sales' website bridge signs short-lived HS256 chatbot tokens with subject
`sales-website-bridge`, scope `chat:turn`, and `customer_id`. Match its configured
signing key to the chatbot's key. The current Sales bridge does not emit issuer or
audience claims, so leave chatbot `JWT_ISSUER` and `JWT_AUDIENCE` unset for that
bridge. Use a dedicated key/instance per tenant. Never expose an internal Sales
service token to a browser.

The signed customer UUID is the existing Sales customer, not a local lead ID or
an email-based match. Every operation verifies the customer and lead in the
configured tenant. Ownership checks bind both shared service subject and customer.
The bridge may call `/api/v1/chat/facts` before the first turn. Only the configured
bridge subject with `chat:turn` gets this narrowly scoped access; facts are fetched
from Sales, rather than trusted from the request body. Staff access still requires
`chat:manage`.

The customer portal needs a customer-scoped chatbot session token from its trusted
host application. The supplied portal accepts it in memory; it does not accept a
Sales service token or place tokens in URLs. Sales' website currently forwards chat
bubbles; embedding portal controls or proxying their endpoints into that website
is an integration task for the website owner.

## Booking lifecycle

All routes below use `/api/v1/customer/threads/{thread_id}`.

| Operation | Route / behavior |
| --- | --- |
| Read policy | `GET /appointments/policy`; Sales timezone, duration, horizon and availability |
| Read/save contact | `GET/POST /appointments/contact`; name and E.164 phone required; only name, phone and supplied email can change |
| Find times | `GET /appointments/availability?day=YYYY-MM-DD&timezone=Asia/Karachi`; up to 20 suggestions at or after that date in the customer's timezone |
| View appointments | `GET /appointments`; current Sales results for the verified lead |
| Book | `POST /appointments`; explicit ISO timestamp with offset and IANA timezone |
| Reschedule | Same POST with Sales appointment UUID, current `expected_version`, and a new `request_id` |
| Accept offer | `POST /appointments/{id}/confirm` with current version and new request UUID |
| Cancel | `DELETE /appointments/{id}?expected_version=N&request_id=UUID`; optional reason |
| Recover | `GET /appointments/operations`, then `POST /appointments/operations/{request_id}/retry` |

For a new booking, `appointment_id` is a client-generated operation UUID
(or supply a separate `request_id`); **the returned ID is the Sales appointment
ID**. Existing appointment mutations require a new request UUID and positive
version. Reuse exactly the same ID and body after a timeout. Never generate a fresh
ID merely because a response was lost. The portal preserves IDs while retrying;
the database journal recovers pending operations after process/browser restarts.

The appointment request's legacy name/email/phone fields do not overwrite Sales.
Save contact details explicitly first. Lead creation always attaches to the
existing authenticated customer. A subsequent read recovers a lead after a lost
response; it never creates another customer by email or phone.

Sales lead creation updates the chatbot's lead state with the authoritative ID.
Existing chatbot estimate/document drafts retain their conversation/customer link
and preserve the Sales lead reference in source slots or metadata. External IDs
are never written into the chatbot's local `sales_leads` foreign key.

Sales alone decides staff eligibility, times, holidays/policy, overlaps, and status.
An unavailable exact time may return `proposed`: it is **not booked**. The portal
displays the alternative and expiry and requires a separate acceptance click.
Sales checks version, expiry and availability again on confirmation. Optimistic
conflicts require a refresh. A historical idempotency receipt is followed by a
current Sales read, preventing replay from resurrecting a canceled appointment.

Chat scheduling directs the customer to an explicit booking-form selection.
It does not silently normalize natural-language dates with standalone Chicago
hours. Before each chat turn, current Sales appointments refresh the chatbot state.
Sales errors never trigger standalone booking. Legacy appointment callbacks must
reference a Sales UUID and are verified against the current Sales state.

In Sales mode, the standalone CSR outbox and its replay endpoint are disabled.
Reminder delivery belongs to the ERP Communication consumer; the portal neither
submits unsupported reminder fields nor promises delivery. Configure and verify
that consumer separately. Staff appointment details use live Sales results.
Local conversation analytics do not pretend local lead/appointment counts are
Sales-wide totals.

## Data lifecycle

Deleting a chatbot conversation erases its chatbot data. The response explicitly
states `scope=chatbot_conversation` and `sales_records=retained_in_sales`.
It does not cancel appointments or request deletion of an entire Sales customer.
Use Sales' `DataPrivacyRequest` workflow for an explicit customer-account
`Forget` or `Export` request and track its real completion there. The chatbot
does not invent a thread-erasure REST endpoint or report a submitted privacy
request as completed. Minimal customer ownership remains on the deletion tombstone.

## Deployment preflight

Apply `.env.sales.example` over the normal Chatbot environment. Its database stays
separate from Sales. Mount the credential directory read-only, without a Kubernetes
`subPath` file mount, so atomic credential rotation becomes visible to new calls.
Sales must register the issuer's Ed25519 public key under the token's `kid`.
Keep the verified tenant and service actor unchanged during retries. If Sales uses
an own/team record policy, give the dedicated chatbot service actor an explicit
tenant-wide policy: customers and appointments span multiple CSR owners. Do not
grant administrative tool scopes to accomplish this.

Run this from the Chatbot runtime with its actual environment and credential mount:

```sh
python scripts/ops/check_sales_connection.py --require-booking
```

The command returns a nonzero exit status for standalone mode, missing credentials,
missing scopes/tools, rejected service authentication, malformed policy, or disabled
booking. Output excludes tokens and customer data. It does not establish that staff
have availability or that reminders were delivered. Verify those with the booking
journey and the owning Communication service before rollout.

## Cross-repository verification

The database acceptance suite targets the migrated Sales schema on
`codex/legacy-sales-migration` (baseline `284da9f`, plus the MCP lifecycle fix).
Use the sibling `erp-contracts` checkout required by the Sales package manifest.
Tests cover the real Python transport, customer endpoints, version conflicts,
proposals, cancellation, contact reuse, shared-subject isolation, token rotation,
malformed JSON-RPC, and durable lost-response replay. Additional SDK tests cover
handshake ordering, negotiated headers, discovery pagination, JSON/SSE results,
credential isolation during concurrent calls, cancellation, size limits, log
redaction, and HTTP errors without redirects or automatic write retries.

An isolated source check is reproducible without generating Prisma. Install the
trusted Sales checkout's dependencies (and its sibling contracts package), using
Node.js 22.15+ for the isolated module hooks, then:

```sh
mkdir -p .review-contract
node scripts/ops/sales_contract_server.mjs /path/to/erp-sales-backend .review-contract
# In another terminal, before the five-minute synthetic token expires:
python scripts/ops/check_sales_contract.py .review-contract/connection.json
```

This exercises all 11 allowed SDK tool calls through Sales' actual MCP router,
including initialization and discovery, EdDSA middleware, coarse MCP gate,
permission map and Zod input schemas. Process-local module hooks substitute
schema-validating echo handlers and the Prisma-dependent error sanitizer only;
the Sales checkout is not modified. It does not run database business logic or
validate Prisma error handling. Stop the probe server afterward.

The former Prisma conflict and missing `sales_current_tenant()` migration blockers
are resolved on that Sales branch. Run the real database suite after installing
Sales dependencies and generating its Prisma client:

```sh
export SALES_TEST_CHECKOUT=/path/to/erp-sales-backend
export SALES_TEST_ADMIN_URL=postgresql://postgres:test-only@127.0.0.1:5432/postgres
python -m pytest tests/integration/test_sales_database.py -q
```

The fixture accepts only a loopback PostgreSQL administrator connection. Each test
creates a uniquely named empty database, replays every Sales migration, seeds
synthetic customers/staff, and runs Sales with a non-superuser role subject to RLS.
It uses the actual Sales website token generator, EdDSA middleware, MCP router,
Prisma services, and the Chatbot's SDK and customer API. It checks contact/lead
reuse, booking, rescheduling, cancellation, optimistic conflicts, proposals,
lost-response recovery, durable events, and customer/tenant isolation. The generated
database and role are removed on normal teardown. The Chatbot journal uses SQLite
here; its PostgreSQL persistence has a separate integration suite.

These tests use synthetic identities and no live LLM, Redis website session, RBAC
directory, or Communication provider. They validate the Chatbot/Sales booking
boundary without claiming that the production deployment or reminder delivery is
enabled. Missing test environment variables explicitly skip this optional suite.

Sales sites configured with `chatMode: "human"` persist visitor messages for staff
without calling the Chatbot and do not require a bot URL or signing secret. AI
sites default to `chatMode: "ai"` and still require both. The public site config
exposes the selected mode so the widget describes the correct behavior.
