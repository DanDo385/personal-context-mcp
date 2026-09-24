-- The wiki graph: links between notes and Maps of Content (MOCs).
--
-- The graph lives here, not in Markdown. Obsidian receives a rendering of it
-- (a generated "Related" section per note and one note per MOC), so the graph
-- can be rebuilt or reclassified without rewriting what a person wrote.

CREATE TABLE IF NOT EXISTS note_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_note_id  UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target_note_id  UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,          -- see WIKI_RELATION_CRITERIA
    confidence      REAL,
    status          TEXT NOT NULL DEFAULT 'accepted'
                      CHECK (status IN ('accepted', 'review', 'rejected')),
    source          TEXT NOT NULL,          -- 'title_mention' | 'jev' | 'human'
    classification_event_id UUID REFERENCES classification_events(id) ON DELETE SET NULL,
    questions_version TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_note_id <> target_note_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_edges_pair_key
    ON note_edges (source_note_id, target_note_id);
CREATE INDEX IF NOT EXISTS note_edges_target_idx ON note_edges (target_note_id);

CREATE TABLE IF NOT EXISTS mocs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    description   TEXT,
    parent_moc_id UUID REFERENCES mocs(id) ON DELETE SET NULL,
    vault_path    TEXT,
    source        TEXT NOT NULL DEFAULT 'human',   -- 'human' | 'topics'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_tsv    TSVECTOR GENERATED ALWAYS AS
                    (to_tsvector('english', title || ' ' || coalesce(description, ''))) STORED
);
CREATE INDEX IF NOT EXISTS mocs_search_idx ON mocs USING gin (search_tsv);

CREATE TABLE IF NOT EXISTS moc_memberships (
    moc_id      UUID NOT NULL REFERENCES mocs(id) ON DELETE CASCADE,
    note_id     UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    section     TEXT NOT NULL DEFAULT 'other',  -- see MOC_SECTION_CRITERIA
    membership  TEXT NOT NULL DEFAULT 'primary' CHECK (membership IN ('primary', 'secondary')),
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'accepted'
                  CHECK (status IN ('accepted', 'review', 'rejected')),
    classification_event_id UUID REFERENCES classification_events(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (moc_id, note_id)
);
CREATE INDEX IF NOT EXISTS moc_memberships_note_idx ON moc_memberships (note_id);

-- Rendering state: what the worker last wrote, and when the graph around a
-- note or MOC was last rendered (wiki render only re-queues what changed).
ALTER TABLE mocs ADD COLUMN IF NOT EXISTS rendered_sha256 TEXT;
ALTER TABLE mocs ADD COLUMN IF NOT EXISTS rendered_at TIMESTAMPTZ;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS wiki_rendered_at TIMESTAMPTZ;
