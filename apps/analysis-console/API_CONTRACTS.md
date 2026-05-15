# Admin API Contracts

Base path:

```text
/api/admin/analysis
```

Authentication:

```http
Authorization: Bearer <BOOKCRAFT_ADMIN_ANALYSIS_TOKEN>
```

## Health

```http
GET /api/admin/analysis/health
```

## Reports

```http
GET  /api/admin/analysis/reports/production
GET  /api/admin/analysis/reports/trimatch-context
POST /api/admin/analysis/evals/context-candidate/run
```

## Rules

```http
GET   /api/admin/analysis/rules/active
GET   /api/admin/analysis/rules/candidates
POST  /api/admin/analysis/rules/candidates
PATCH /api/admin/analysis/rules/candidates/{candidate_id}
```

## Rules Army v2

```http
POST /api/admin/analysis/rules-army-v2/preflight
POST /api/admin/analysis/rules-army-v2/activate
POST /api/admin/analysis/rules-army-v2/rollback
```

Activation payload:

```json
{
  "confirm_phrase": "I_UNDERSTAND_THIS_PROMOTES_RULES_ARMY_V2",
  "mode": "active",
  "force": false
}
```

Rollback payload:

```json
{
  "backup_dir": "data/trimatch/backups/20260515_123456_rules"
}
```

## Live trace endpoints

```http
GET /api/admin/analysis/traces/latest?limit=50
GET /api/admin/analysis/traces/{thread_id}?limit=100

Both endpoints require:

Authorization: Bearer <BOOKCRAFT_ADMIN_ANALYSIS_TOKEN>

The response contains redacted per-turn trace snapshots written by ChatService.
