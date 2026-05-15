# Architecture

## Principle

The console is report-first and read-only. The goal is to approve the operator experience before adding backend mutation endpoints.

## Data flow

```text
JSON report upload
  -> parseReportJson()
  -> local state + localStorage session cache
  -> dashboard/pages
  -> optional export session
```

## Report types

- `PerformanceReport`: production/threaded component reports.
- `ContextCandidateReport`: Tri-Match advanced context diagnostics.
- Unknown JSON is rejected with a clear error.

## State

The app uses local React state and localStorage:

```text
bookcraft.analysis.reports.v2
bookcraft.analysis.rules.v2
```

No data is sent to a server.

## Runtime atom model

The UI understands runtime context atoms recently added to the backend:

```text
services
negated_services
negated_terms
context_markers
forbid_markers
query_cues
service_mentions
word_counts
page_counts
currency
urls
emails
phones
manuscript_status
```

## Rule governance model

Rule candidates are local mock objects with these states:

```text
draft
needs_review
approved_for_staging
changes_requested
rejected
promoted_to_staged
blocked
```

Backend integration should persist candidates, reviews, collision scans, eval results, and promotion decisions.

## Safety posture

- Rules Army v2 remains inactive.
- `valid_for_active_promotion=false` is shown clearly.
- Rule approval actions are local-only until backend endpoints exist.
- Generated reports are not uploaded anywhere unless the user explicitly integrates backend APIs later.
