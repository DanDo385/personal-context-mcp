-- 0002_core.sql
-- Conversations, messages, summaries, memories, projects, decisions, tasks,
-- lists, entities and notes.
--
-- Embedding dimension is fixed at 1536 (OpenAI text-embedding-3-small,
-- Gemini gemini-embedding-001 truncated to 1536). Changing it requires a
-- migration that drops and recreates every `embedding` column and its index;
-- see README "Embeddings".

-- ---------------------------------------------------------------- providers

CREATE TABLE IF NOT EXISTS providers (
    id          TEXT PRIMARY KEY,           -- 'chatgpt' | 'claude' | 'gemini' | 'grok' | 'system'
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO providers (id, name) VALUES
    ('chatgpt', 'ChatGPT'),
    ('claude',  'Claude'),
    ('gemini',  'Gemini'),
    ('grok',    'Grok'),
    ('system',  'Personal Context')
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------- projects

CREATE TABLE IF NOT EXISTS projects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | paused | done | archived
    goals         TEXT,
    sensitivity   pc_sensitivity NOT NULL DEFAULT 'normal',
    vault_path    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_provider TEXT REFERENCES providers(id),
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS projects_status_idx ON projects (status, last_active_at DESC);
DROP TRIGGER IF EXISTS projects_touch ON projects;
CREATE TRIGGER projects_touch BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- ------------------------------------------------------------ conversations

CREATE TABLE IF NOT EXISTS conversations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider                 TEXT NOT NULL REFERENCES providers(id),
    provider_conversation_id TEXT,
    model                    TEXT,
    title                    TEXT,
    summary                  TEXT,
    project_id               UUID REFERENCES projects(id) ON DELETE SET NULL,
    sensitivity              pc_sensitivity NOT NULL DEFAULT 'normal',
    imported                 BOOLEAN NOT NULL DEFAULT false,
    import_key               TEXT,           -- deterministic key for idempotent import
    started_at               TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- The idempotency anchor for imports and checkpoints: one row per
-- (provider, provider_conversation_id). Partial index so many conversations
-- may have a NULL provider id without colliding.
CREATE UNIQUE INDEX IF NOT EXISTS conversations_provider_extid_key
    ON conversations (provider, provider_conversation_id)
    WHERE provider_conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS conversations_project_idx ON conversations (project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS conversations_updated_idx ON conversations (updated_at DESC);
DROP TRIGGER IF EXISTS conversations_touch ON conversations;
CREATE TRIGGER conversations_touch BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- ----------------------------------------------------------------- messages

CREATE TABLE IF NOT EXISTS messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL REFERENCES providers(id),
    provider_message_id TEXT,
    model               TEXT,
    role                TEXT NOT NULL,      -- user | assistant | system | tool
    content             TEXT NOT NULL,
    content_sha256      TEXT NOT NULL,
    provenance          pc_provenance NOT NULL,
    sequence            INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding           VECTOR(1536),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_conv_seq_key ON messages (conversation_id, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS messages_provider_msgid_key
    ON messages (conversation_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS messages_search_idx ON messages USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS messages_conv_idx ON messages (conversation_id, sequence);

-- ------------------------------------------------------- session summaries

CREATE TABLE IF NOT EXISTS session_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL REFERENCES providers(id),
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,
    title           TEXT,
    summary         TEXT NOT NULL,
    summary_sha256  TEXT NOT NULL,
    open_questions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    important_context JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance      pc_provenance NOT NULL DEFAULT 'assistant_generated',
    sensitivity     pc_sensitivity NOT NULL DEFAULT 'normal',
    classification_status pc_classification_status NOT NULL DEFAULT 'unclassified',
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding       VECTOR(1536),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv      TSVECTOR GENERATED ALWAYS AS
                      (to_tsvector('english', coalesce(title,'') || ' ' || summary)) STORED
);
-- One live summary per conversation; checkpoints update it in place.
CREATE UNIQUE INDEX IF NOT EXISTS session_summaries_conv_key ON session_summaries (conversation_id);
CREATE INDEX IF NOT EXISTS session_summaries_search_idx ON session_summaries USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS session_summaries_updated_idx ON session_summaries (updated_at DESC);
DROP TRIGGER IF EXISTS session_summaries_touch ON session_summaries;
CREATE TRIGGER session_summaries_touch BEFORE UPDATE ON session_summaries
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- ----------------------------------------------------------------- memories

CREATE TABLE IF NOT EXISTS memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content         TEXT NOT NULL,
    content_sha256  TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'fact',   -- fact | preference | relationship | decision | knowledge
    subject         TEXT,                            -- normalized topic/entity handle for dedup
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    provenance      pc_provenance NOT NULL,
    sensitivity     pc_sensitivity NOT NULL DEFAULT 'normal',
    importance      REAL NOT NULL DEFAULT 0.5,       -- 0..1, from Jev Score or rules
    confidence      REAL,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | superseded | retracted
    supersedes_id   UUID REFERENCES memories(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    use_count       INTEGER NOT NULL DEFAULT 0,
    embedding       VECTOR(1536),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv      TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
-- Exact-duplicate guard: identical text for the same subject is one memory.
CREATE UNIQUE INDEX IF NOT EXISTS memories_sha_subject_key
    ON memories (content_sha256, coalesce(subject, ''));
CREATE INDEX IF NOT EXISTS memories_search_idx ON memories USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS memories_status_idx ON memories (status, importance DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS memories_project_idx ON memories (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS memories_subject_trgm_idx ON memories USING GIN (subject gin_trgm_ops);
DROP TRIGGER IF EXISTS memories_touch ON memories;
CREATE TRIGGER memories_touch BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- Full memory history. Nothing is destructively overwritten.
CREATE TABLE IF NOT EXISTS memory_revisions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    provenance  pc_provenance NOT NULL,
    changed_by_provider TEXT REFERENCES providers(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_revisions_key ON memory_revisions (memory_id, version);

-- --------------------------------------------------- project events / log

CREATE TABLE IF NOT EXISTS project_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,          -- status_change | milestone | note | blocker | update
    summary     TEXT NOT NULL,
    detail      TEXT,
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    provenance  pc_provenance NOT NULL DEFAULT 'assistant_generated',
    event_sha256 TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS project_events_dedup_key
    ON project_events (project_id, event_sha256);
CREATE INDEX IF NOT EXISTS project_events_project_idx ON project_events (project_id, created_at DESC);

-- ---------------------------------------------------------------- decisions

CREATE TABLE IF NOT EXISTS decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    statement       TEXT NOT NULL,
    statement_sha256 TEXT NOT NULL,
    rationale       TEXT,
    project_id      UUID REFERENCES projects(id) ON DELETE SET NULL,
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    provenance      pc_provenance NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',   -- active | superseded | reversed
    supersedes_id   UUID REFERENCES decisions(id) ON DELETE SET NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding       VECTOR(1536),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv      TSVECTOR GENERATED ALWAYS AS
                      (to_tsvector('english', statement || ' ' || coalesce(rationale,''))) STORED
);
CREATE UNIQUE INDEX IF NOT EXISTS decisions_dedup_key ON decisions (statement_sha256);
CREATE INDEX IF NOT EXISTS decisions_search_idx ON decisions USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS decisions_project_idx ON decisions (project_id, decided_at DESC);
DROP TRIGGER IF EXISTS decisions_touch ON decisions;
CREATE TRIGGER decisions_touch BEFORE UPDATE ON decisions
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- -------------------------------------------------------------------- lists
-- Lists are first-class. Postgres is canonical; Obsidian renders a view.

CREATE TABLE IF NOT EXISTS lists (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    list_type   TEXT NOT NULL DEFAULT 'general',  -- task | reading | shopping | idea | people | general
    project_id  UUID REFERENCES projects(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'active',   -- active | archived
    vault_path  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv  TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', name || ' ' || coalesce(description,''))) STORED
);
CREATE INDEX IF NOT EXISTS lists_search_idx ON lists USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS lists_name_trgm_idx ON lists USING GIN (name gin_trgm_ops);
DROP TRIGGER IF EXISTS lists_touch ON lists;
CREATE TRIGGER lists_touch BEFORE UPDATE ON lists
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

CREATE TABLE IF NOT EXISTS list_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    list_id      UUID NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    text_sha256  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',    -- open | done | dropped | blocked
    priority     INTEGER NOT NULL DEFAULT 0,      -- higher is more urgent
    position     INTEGER NOT NULL DEFAULT 0,
    due_date     DATE,
    notes        TEXT,
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    provenance   pc_provenance NOT NULL DEFAULT 'user_statement',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    vault_synced_at TIMESTAMPTZ,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv   TSVECTOR GENERATED ALWAYS AS
                   (to_tsvector('english', text || ' ' || coalesce(notes,''))) STORED
);
-- Same text twice on the same list is the same item (case-normalized upstream).
CREATE UNIQUE INDEX IF NOT EXISTS list_items_dedup_key ON list_items (list_id, text_sha256);
CREATE INDEX IF NOT EXISTS list_items_list_idx ON list_items (list_id, status, position);
CREATE INDEX IF NOT EXISTS list_items_search_idx ON list_items USING GIN (search_tsv);
DROP TRIGGER IF EXISTS list_items_touch ON list_items;
CREATE TRIGGER list_items_touch BEFORE UPDATE ON list_items
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

-- Tasks are list items on a project's task list, plus scheduling fields.
-- Keeping one table avoids two parallel to-do systems drifting apart.
CREATE OR REPLACE VIEW tasks AS
    SELECT li.id, li.list_id, l.project_id, li.text, li.status, li.priority,
           li.due_date, li.notes, li.created_at, li.completed_at, li.metadata
    FROM list_items li
    JOIN lists l ON l.id = li.list_id
    WHERE l.list_type = 'task';

-- ----------------------------------------------------- entities & relations

CREATE TABLE IF NOT EXISTS entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'concept',  -- person | org | concept | tool | place
    description TEXT,
    sensitivity pc_sensitivity NOT NULL DEFAULT 'normal',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   VECTOR(1536),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv  TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', name || ' ' || coalesce(description,''))) STORED
);
CREATE INDEX IF NOT EXISTS entities_search_idx ON entities USING GIN (search_tsv);
DROP TRIGGER IF EXISTS entities_touch ON entities;
CREATE TRIGGER entities_touch BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

CREATE TABLE IF NOT EXISTS entity_relationships (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation     TEXT NOT NULL,
    provenance   pc_provenance NOT NULL DEFAULT 'assistant_generated',
    confidence   REAL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS entity_relationships_key
    ON entity_relationships (from_entity_id, to_entity_id, relation);

-- -------------------------------------------------------------------- notes

CREATE TABLE IF NOT EXISTS notes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    note_class   TEXT NOT NULL DEFAULT 'inbox',  -- project|research|wiki|concept|resource|daily|inbox
    project_id   UUID REFERENCES projects(id) ON DELETE SET NULL,
    vault_path   TEXT,                            -- validated, server-generated
    sensitivity  pc_sensitivity NOT NULL DEFAULT 'normal',
    classification_status pc_classification_status NOT NULL DEFAULT 'unclassified',
    source_provider TEXT REFERENCES providers(id),
    source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    provenance   pc_provenance NOT NULL DEFAULT 'assistant_generated',
    citations    JSONB NOT NULL DEFAULT '[]'::jsonb,
    version      INTEGER NOT NULL DEFAULT 1,
    vault_synced_sha256 TEXT,                     -- sha of the copy the vault last had
    vault_synced_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding    VECTOR(1536),
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_tsv   TSVECTOR GENERATED ALWAYS AS
                   (to_tsvector('english', title || ' ' || content)) STORED
);
CREATE INDEX IF NOT EXISTS notes_search_idx ON notes USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS notes_project_idx ON notes (project_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS notes_vault_path_key ON notes (vault_path) WHERE vault_path IS NOT NULL;
DROP TRIGGER IF EXISTS notes_touch ON notes;
CREATE TRIGGER notes_touch BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION pc_touch_updated_at();

CREATE TABLE IF NOT EXISTS note_versions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id     UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT 'system',   -- provider id, 'human' for vault edits
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS note_versions_key ON note_versions (note_id, version);
