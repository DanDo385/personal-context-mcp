-- 0004_ops.sql
-- Sync jobs, classification log, audit log, review queue, OAuth state.

-- ------------------------------------------------------------- sync to vault
-- The cloud service NEVER reaches into the Mac. It enqueues jobs here; the
-- local worker polls outbound and acknowledges.

CREATE TABLE IF NOT EXISTS sync_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type      TEXT NOT NULL,
        -- write_note | write_list | write_artifact_note | mirror_artifact_files | delete_note
    object_type   TEXT NOT NULL,   -- note | list | artifact
    object_id     UUID NOT NULL,
    vault_path    TEXT NOT NULL,   -- validated server-side against the allowed roots
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_sha256 TEXT,          -- optimistic concurrency: what we think is on disk
    content_sha256  TEXT,          -- what we want on disk
    status        TEXT NOT NULL DEFAULT 'queued',
        -- queued | approved | leased | done | failed | conflict | cancelled
    requires_approval BOOLEAN NOT NULL DEFAULT false,
    attempts      INTEGER NOT NULL DEFAULT 0,
    lease_owner   TEXT,
    leased_until  TIMESTAMPTZ,
    last_error    TEXT,
    source_provider TEXT REFERENCES providers(id),
    classification_event_id UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS sync_jobs_pending_idx
    ON sync_jobs (status, created_at) WHERE status IN ('queued', 'approved');
-- One outstanding job per target path keeps the worker's writes ordered and
-- stops a burst of checkpoints from queueing ten writes of the same note.
CREATE UNIQUE INDEX IF NOT EXISTS sync_jobs_open_path_key
    ON sync_jobs (vault_path)
    WHERE status IN ('queued', 'approved', 'leased');
DROP TRIGGER IF EXISTS sync_jobs_touch ON sync_jobs;
CREATE TRIGGER sync_jobs_touch BEFORE UPDATE ON sync_jobs
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

CREATE TABLE IF NOT EXISTS sync_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_job_id UUID REFERENCES sync_jobs(id) ON DELETE CASCADE,
    direction   TEXT NOT NULL,       -- outbound | inbound
    event_type  TEXT NOT NULL,       -- leased | written | acked | conflict | failed | human_edit
    vault_path  TEXT,
    sha256      TEXT,
    worker_id   TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS sync_events_job_idx ON sync_events (sync_job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sync_events_created_idx ON sync_events (created_at DESC);

-- ------------------------------------------------ classification event log
-- Every Jev (and every rules-fallback) routing decision is recorded so the
-- quality of classification can actually be evaluated later.

CREATE TABLE IF NOT EXISTS classification_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type     TEXT NOT NULL,   -- checkpoint | conversation | artifact | note | list_candidate
    object_id       UUID,
    classifier      TEXT NOT NULL,   -- 'jev' | 'rules'
    model           TEXT,            -- e.g. 'jev-1.13.0' as reported by the API
    request_id      TEXT,            -- x-typesafe-request-id
    questions_version TEXT NOT NULL, -- version of our question definitions
    input_hash      TEXT NOT NULL,   -- sha256 of the state we sent
    answers         JSONB NOT NULL,  -- {key: {type, value, confidence, probabilities}}
    confidence      REAL,
    action_taken    TEXT NOT NULL,   -- auto_routed | review | deferred | none | fallback
    degraded        BOOLEAN NOT NULL DEFAULT false,  -- true when Jev was unavailable
    overridden_by_user BOOLEAN NOT NULL DEFAULT false,
    override_note   TEXT,
    input_tokens    INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS classification_events_object_idx
    ON classification_events (object_type, object_id, created_at DESC);
CREATE INDEX IF NOT EXISTS classification_events_created_idx
    ON classification_events (created_at DESC);
-- Re-running the identical classification is cheap to detect.
CREATE INDEX IF NOT EXISTS classification_events_input_idx ON classification_events (input_hash);

-- Work queue for objects whose classification failed or was deferred.
CREATE TABLE IF NOT EXISTS reclassification_queue (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type TEXT NOT NULL,
    object_id   UUID NOT NULL,
    reason      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS reclassification_queue_key
    ON reclassification_queue (object_type, object_id);

-- --------------------------------------------------------- human review queue

CREATE TABLE IF NOT EXISTS review_queue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type   TEXT NOT NULL,
    object_id     UUID NOT NULL,
    reason        TEXT NOT NULL,
        -- ambiguous_project | ambiguous_destination | uncertain_memory |
        -- sensitive_artifact | note_conflict | suspicious_archive | low_confidence
    question      TEXT NOT NULL,
    options       JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggested     JSONB,
    confidence    REAL,
    classification_event_id UUID REFERENCES classification_events(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'open',   -- open | approved | reclassified | dismissed
    resolution    JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at   TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS review_queue_open_key
    ON review_queue (object_type, object_id, reason) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS review_queue_status_idx ON review_queue (status, created_at DESC);
DROP TRIGGER IF EXISTS review_queue_touch ON review_queue;
CREATE TRIGGER review_queue_touch BEFORE UPDATE ON review_queue
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- ------------------------------------------------------------------ audit log
-- Answers: who changed this, which model, why, and what did it look like before.

CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      TEXT,
    principal       TEXT,            -- authenticated subject
    client_id       TEXT,            -- OAuth client that called
    provider        TEXT,            -- chatgpt | claude | gemini | grok | worker | cli
    model           TEXT,
    tool            TEXT NOT NULL,
    action          TEXT NOT NULL,   -- create | update | delete | read_sensitive | approve
    object_type     TEXT,
    object_id       UUID,
    conversation_ref TEXT,
    previous_version JSONB,
    resulting_version JSONB,
    classification_event_id UUID REFERENCES classification_events(id) ON DELETE SET NULL,
    approval_status TEXT,            -- not_required | auto | user_approved
    provenance      pc_provenance,
    status          TEXT NOT NULL DEFAULT 'ok',
    error           TEXT,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_object_idx ON audit_log (object_type, object_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_created_idx ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_tool_idx ON audit_log (tool, created_at DESC);

-- ------------------------------------------------------------- oauth kv store
-- FastMCP's OAuth layer needs durable storage for client registrations and
-- encrypted upstream tokens. On Cloud Run the filesystem is ephemeral, so we
-- back it with Postgres (see personal_context_mcp/auth/kv.py).

CREATE TABLE IF NOT EXISTS oauth_kv (
    collection  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, key)
);
CREATE INDEX IF NOT EXISTS oauth_kv_expiry_idx ON oauth_kv (expires_at) WHERE expires_at IS NOT NULL;
DROP TRIGGER IF EXISTS oauth_kv_touch ON oauth_kv;
CREATE TRIGGER oauth_kv_touch BEFORE UPDATE ON oauth_kv
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- ------------------------------------------------------------- rate limiting

CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key  TEXT PRIMARY KEY,
    tokens      REAL NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
