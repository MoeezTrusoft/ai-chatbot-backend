-- BookCraft Pricing & Timeline Engine v2.1 recommended persistence schema.
-- Integrate into the main chatbot migration system and FieldMeta/audit conventions.

CREATE TABLE IF NOT EXISTS pricing_config_versions (
  id UUID PRIMARY KEY,
  config_type TEXT NOT NULL,
  config_version TEXT NOT NULL,
  checksum TEXT NOT NULL,
  source_reference TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  promoted_at TIMESTAMPTZ,
  promoted_by TEXT
);

CREATE TABLE IF NOT EXISTS quote_requests (
  id UUID PRIMARY KEY,
  thread_id UUID NOT NULL,
  customer_id UUID,
  quote_mode TEXT NOT NULL,
  input_snapshot JSONB NOT NULL,
  field_meta_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_results (
  id UUID PRIMARY KEY,
  quote_request_id UUID NOT NULL REFERENCES quote_requests(id),
  status TEXT NOT NULL,
  config_versions JSONB NOT NULL,
  result_snapshot JSONB NOT NULL,
  subtotal_low NUMERIC(12,2) NOT NULL,
  subtotal_high NUMERIC(12,2) NOT NULL,
  total_low NUMERIC(12,2) NOT NULL,
  total_high NUMERIC(12,2) NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  confidence NUMERIC(4,3) NOT NULL,
  human_review_required BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_events (
  id UUID PRIMARY KEY,
  quote_id UUID NOT NULL REFERENCES quote_results(id),
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  previous_hash TEXT,
  event_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
