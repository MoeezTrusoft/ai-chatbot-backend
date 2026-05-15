# User Guide

## 1. Install

```bash
cd apps/analysis-console
npm install
npm run dev
```

Open `http://localhost:5173`.

## 2. Import reports

Use Dashboard or Settings > Import report JSON.

Supported files:

```text
production_component_performance_report.json
production_threaded_component_load_report.json
trimatch_context_candidate_report.json
```

## 3. Read the Dashboard

Use the dashboard to quickly answer:

- Is the report valid?
- What is p95 latency?
- Are there critical issues?
- How many context eval examples passed?
- Which response routes are being used?

## 4. Inspect a conversation turn

Go to Trace Viewer:

- Select a turn.
- Read the user message and response preview.
- Inspect decision layer values.
- Inspect runtime atoms.
- Inspect provider votes.
- Open raw JSON for debugging.

## 5. Inspect Tri-Match diagnostics

Go to Tri-Match:

- Select an eval row.
- Compare expected vs actual checks.
- Inspect rule evidence.
- Confirm `valid_for_active_promotion=false` unless formal promotion is intentionally enabled later.

## 6. Prototype rule review

Go to Rule Approval:

- Select a candidate.
- Review target, layer, confidence, phrase/regex/pattern, collision warnings, and eval score.
- Use local buttons: approve, request changes, reject, block.

These actions are local only.

## 7. Compare reports

Go to Evals & Regression:

- Upload multiple performance reports.
- Compare latest vs previous p95, critical issues, soft warnings, and validity.

## 8. Export local session

Go to Settings and click Export local session.
