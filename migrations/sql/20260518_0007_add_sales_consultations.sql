CREATE TABLE IF NOT EXISTS sales_consultations (
    id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(64) NOT NULL,
    lead_id VARCHAR(64),
    thread_id VARCHAR(64),
    customer_name VARCHAR(255) NOT NULL,
    customer_email VARCHAR(320),
    customer_phone VARCHAR(64),
    services JSONB NOT NULL DEFAULT '[]'::jsonb,
    csr_id VARCHAR(128) NOT NULL,
    csr_name VARCHAR(255) NOT NULL,
    priority_rank INTEGER NOT NULL,
    requested_time_text TEXT NOT NULL,
    customer_timezone VARCHAR(128),
    business_timezone VARCHAR(128) NOT NULL DEFAULT 'America/Chicago',
    starts_at_utc TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    ends_at_utc TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    houston_display_time VARCHAR(255) NOT NULL,
    customer_display_time VARCHAR(255),
    duration_minutes INTEGER NOT NULL DEFAULT 30,
    status VARCHAR(64) NOT NULL DEFAULT 'scheduled',
    source VARCHAR(64) NOT NULL DEFAULT 'chatbot',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_sales_consultations_customer_id
    ON sales_consultations (customer_id);

CREATE INDEX IF NOT EXISTS ix_sales_consultations_thread_id
    ON sales_consultations (thread_id);

CREATE INDEX IF NOT EXISTS ix_sales_consultations_lead_id
    ON sales_consultations (lead_id);

CREATE INDEX IF NOT EXISTS ix_sales_consultations_csr_status_time
    ON sales_consultations (csr_id, status, starts_at_utc, ends_at_utc)
    WHERE cancelled_at IS NULL;
