# Personal Context Browser Extension

Captures AI chat sessions from Claude, ChatGPT, Gemini and Grok into your Personal Context database.

## Supported Browsers

- **Brave** (Chromium-based, recommended)
- **Chrome / Chromium**
- **Arc** (Chromium-based)

Firefox not supported in v1 (Manifest V3 differences).

## Loading Unpacked

1. Open `brave://extensions/` or `chrome://extensions/`
2. Enable **Developer mode** (toggle top-right)
3. Click **Load unpacked**
4. Select the `extension/` folder

## Setup

1. Click the extension icon → the popup opens
2. The extension auto-registers with the Personal Context server
3. Configure the server URL if needed (defaults to MBP over Tailscale)

## Scripts

```bash
npm run dev         # Build in watch mode
npm run build       # Production build
npm run package     # Production build + zip
npm test            # Unit tests
npm run check-sites # Breakage check against saved page fixtures
npm run perf        # Playwright performance run (5ms budget)
npm run backfill    # Dry-run backfill (add --run for real import)
```

## Provider Support

| Provider | Method | Conversation List | Full Fetch | Temp Chat Skip |
|----------|--------|-------------------|------------|----------------|
| Claude   | JSON API | ✅ | ✅ | ✅ `is_temporary` |
| ChatGPT  | JSON API | ✅ | ✅ batch | ⚠️ inferred |
| Gemini   | DOM | ✅ sidebar | ✅ scroll | ⚠️ inferred |
| Grok     | DOM | ✅ sidebar | ✅ scroll | ⚠️ inferred |

## Architecture

- `background.js` — Service worker: alarm-based queue processing, periodic sync
- `content.js` — Content script: provider detection, auto-capture triggers
- `providers/` — Per-provider extractors (API + DOM)
- `lib/api.js` — HTTPS ingest client (Tailscale → MBP, Vercel fallback)
- `lib/queue.js` — Durable capture queue in chrome.storage.local with retries
- `popup/` — Extension popup: status, pause toggle, dead letter queue
- `sidepanel/` — Side panel: project handoff display + clipboard insert

## Permissions

- `storage` — Queue persistence, settings, device token
- `alarms` — Periodic queue processing and sync
- `sidePanel` — Project handoff display
- Host: `claude.ai`, `chatgpt.com`, `gemini.google.com`, `grok.com`

No `clipboardRead`, no `downloads`. Conversations go to the server, not local files.