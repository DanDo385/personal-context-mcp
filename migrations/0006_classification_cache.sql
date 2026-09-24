-- Lookup for the classification cache (ClassificationService._cached): the
-- same state, candidates, question set and model are answered from the most
-- recent non-degraded event instead of asking Jev again.
CREATE INDEX IF NOT EXISTS classification_events_cache_key_idx
    ON classification_events ((metadata->>'cache_key'), created_at DESC)
    WHERE NOT degraded;
