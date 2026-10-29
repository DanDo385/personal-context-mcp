# Browser Extension — Handoff

## What was built

### Extension (`extension/`)
- **4 provider extractors**: Claude + ChatGPT (JSON API with cookie/bearer auth), Gemini + Grok (DOM with multi-signal role detection)
- **Service worker** (`background.js`): alarm-based queue processing (30s), periodic sync (15min), backfill coordination
- **Content script** (`content.js`): auto-capture on finished replies, URL change detection, debounced DOM observation
- **Durable queue** (`lib/queue.js`): `chrome.storage.local` persistence, 3 retries with backoff → dead letter
- **HTTPS client** (`lib/api.js`): sends to Vercel with Bearer token, pairing code flow, never auto-registers on 401
- **Popup**: pairing section (code input + "Pair this browser") when unpaired; pause/resume, per-provider sync times, dead letter when paired
- **Side panel**: project handoff display, clipboard copy (never DOM injection)
- **Fixtures**: synthetic JSON per provider

### Server
- **`ingest.py`**: `POST /api/v1/ingest/conversation` — idempotent upsert, `POST /api/v1/auth/pair-device` — code-based pairing, `DELETE /api/v1/auth/devices/{id}` — revocation, `GET /api/v1/handoff` — project handoff
- **`cli.py`**: `personal-context-mcp pair-device` — generates 8-char pairing code
- **Migrations**: `0008_device_tokens.sql` (device token table), `0009_pairing_codes.sql` (one-time pairing codes, 5-min expiry)

### Tests
- `tests/test_ingest.py`: 16 parametrized integration tests (idempotency, message append, auth, revocation)
- `extension/tests/fixtures/`: 4 synthetic fixture JSON files

### Verification
- ruff + mypy clean on entire codebase
- 315 existing tests pass, 125 skip (need test DB)
- Ingest tested live on Vercel: all 4 providers create conversations, messages inserted, dedup verified (re-send → 0 inserted)

## What is NOT done yet

### Needs a browser (manual or Playwright)
- Load the extension unpacked in Brave at `chrome://extensions/`
- Pair with `personal-context-mcp pair-device`
- Test save-on-click against the live server
- Run backfill dry run (lists counts per provider)
- Run real backfill in batches (after dry run approved)
- Verify ongoing capture (auto-capture on finished replies)

### Needs a running MBP server
- Point extension at MBP over Tailscale for lower latency
- The current default is Vercel (`https://personal-context-mcp-seven.vercel.app`)

### Performance tests
- `npm run perf` — Playwright with Chrome tracing, 200-message chats, 5ms INP budget

### Breakage tests
- `npm run check-sites` — daily selector check against saved page fixtures
- CI workflow not yet created for breakage tests

### Migration to production
- `0009_pairing_codes.sql` not yet applied to production Supabase DB

### Worktree cleanup
- Do NOT delete this worktree yet (`feat-browser-extension`)
- Safe to delete after: production migration applied, extension loaded and paired in Brave, backfill dry run complete

## How to load in Brave

1. Open `brave://extensions/`
2. Enable **Developer mode** (toggle top-right)
3. Click **Load unpacked**
4. Select the `extension/` folder from this repo
5. The Personal Context icon appears in the toolbar
6. Click it → "Not paired" section appears
7. On the server: `personal-context-mcp pair-device`
8. Enter the 8-character code in the popup → paired

## Backfill steps

1. Ensure the extension is loaded and paired
2. Open a tab for each provider you want to backfill (claude.ai, chatgpt.com, etc.)
3. Run the backfill dry run:
   ```bash
   npm run backfill
   ```
4. Review the dry-run counts per provider
5. Run the real backfill:
   ```bash
   npm run backfill -- --run
   ```
   This imports in small batches (one request per 2 seconds per provider), resumes after failures

## Server endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/auth/pair-device` | none | Pair browser with code → device token |
| DELETE | `/api/v1/auth/devices/{id}` | Bearer | Revoke own device token |
| POST | `/api/v1/ingest/conversation` | Bearer | Upsert conversation + messages |
| GET | `/api/v1/handoff` | Bearer | Project handoff for side panel |