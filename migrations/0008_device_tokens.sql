-- Device tokens for browser extension authentication.
-- One token per browser instance, revocable on the server.

CREATE TABLE IF NOT EXISTS device_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id   TEXT NOT NULL UNIQUE,
    token       TEXT NOT NULL UNIQUE,
    device_name TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS device_tokens_token_idx ON device_tokens (token) WHERE revoked_at IS NULL;