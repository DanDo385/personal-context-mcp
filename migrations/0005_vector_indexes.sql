-- 0005_vector_indexes.sql
-- HNSW indexes for cosine similarity. HNSW (unlike ivfflat) builds fine on an
-- empty table, so these can be created up front.
--
-- If EMBEDDING_PROVIDER=none these columns stay NULL and the indexes cost
-- nothing; retrieval falls back to full-text search only.

CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS session_summaries_embedding_idx
    ON session_summaries USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS notes_embedding_idx
    ON notes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS decisions_embedding_idx
    ON decisions USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS messages_embedding_idx
    ON messages USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS entities_embedding_idx
    ON entities USING hnsw (embedding vector_cosine_ops);
