-- 0009_note_class_check.sql
-- Enforce the note_class vocabulary in the database, not only in the server.
-- Values outside it become 'inbox'; the old values are kept in
-- note_class_repairs so the repair can be undone.
--
-- Rollback (by hand; the runner has no down migrations):
--   ALTER TABLE notes DROP CONSTRAINT IF EXISTS notes_note_class_check;
--   UPDATE notes n SET note_class = r.old_class
--     FROM note_class_repairs r WHERE r.note_id = n.id;
--   DROP TABLE IF EXISTS note_class_repairs;
--   DELETE FROM schema_migrations WHERE version = '0009_note_class_check';

CREATE TABLE IF NOT EXISTS note_class_repairs (
    note_id     UUID PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    old_class   TEXT NOT NULL,
    repaired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO note_class_repairs (note_id, old_class)
SELECT id, note_class FROM notes
WHERE note_class NOT IN ('project','research','wiki','concept','resource','daily','inbox','session_index')
ON CONFLICT (note_id) DO NOTHING;

UPDATE notes SET note_class = 'inbox'
WHERE id IN (SELECT note_id FROM note_class_repairs);

ALTER TABLE notes DROP CONSTRAINT IF EXISTS notes_note_class_check;
ALTER TABLE notes ADD CONSTRAINT notes_note_class_check
    CHECK (note_class IN ('project','research','wiki','concept','resource','daily','inbox','session_index'));
