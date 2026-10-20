-- Immutable previews and durable send receipts outlive AgentMail's 24h send-key window.
INSERT INTO providers (id, name) VALUES ('codex', 'Codex') ON CONFLICT (id) DO NOTHING;

CREATE TABLE email_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    confirmation TEXT NOT NULL,
    dry_run BOOLEAN NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent', 'scheduled', 'dry_run')),
    dispatch_started_at TIMESTAMPTZ,
    receipt JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner, idempotency_key)
);

-- Give existing content one home without moving any files or guessing concepts.
UPDATE conversations c SET metadata = c.metadata || jsonb_build_object(
    'home', coalesce((SELECT 'projects/' || p.slug FROM projects p WHERE p.id = c.project_id), 'other'),
    'tags', coalesce(c.metadata->'tags', '[]'::jsonb) || jsonb_build_array(coalesce((SELECT 'projects/' || p.slug FROM projects p WHERE p.id = c.project_id), 'other'))
) WHERE NOT (c.metadata ? 'home');

UPDATE notes n SET metadata = n.metadata || jsonb_build_object(
    'home', coalesce((SELECT 'projects/' || p.slug FROM projects p WHERE p.id = n.project_id), 'other'),
    'tags', coalesce(n.metadata->'tags', '[]'::jsonb) || jsonb_build_array(coalesce((SELECT 'projects/' || p.slug FROM projects p WHERE p.id = n.project_id), 'other'))
) WHERE NOT (n.metadata ? 'home');
