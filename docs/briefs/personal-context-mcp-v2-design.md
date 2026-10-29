# personal-context-mcp v2 design brief

Updated brief: 2026-10-17. Status: parked, proposals and prototypes only.

## Session checkpoint

No v2 implementation, design, benchmark, prototype, or production change was made in this session. The rebuilt worktree was verified on branch `v2-design` at `2ca2095`. Work remains parked until the context_search fix, browser extension, and server skills have landed and the owner supplies an updated brief. This document preserves the brief and subsequent instructions so the session can close. It does not claim that any proposed behavior exists.

The later extension pairing-code request was not implemented here: extension work belongs to its own session. No worktree was deleted. The Personal Context MCP `session_checkpoint` tool is unavailable in this harness; this durable checkpoint is not a substitute for a completed remote checkpoint.

## Changes since the 2026-10-13 brief

- The browser extension is built in `feat-browser-extension`. v2 designs and reviews its ingestion API, not another extension.
- The vault is back in scope as a database reader, using the layout chosen on 2026-10-17.
- AgentMail sending and explicitly requested scheduled sending are in scope.
- Development time, effort, and cost are not selection criteria. The two-day time box is removed.
- The separate bootstrap fix defines the minimum behavior for v2 `start()`.
- Global worktree, engineering, and contribution rules apply.

## Role and design standard

Rebuild personal-context-mcp from first principles as proposals and prototypes only. Language, framework, host, database, transport, and tools may all change. Judge designs against John Ousterhout's *A Philosophy of Software Design*, second edition. Design the interface twice and compare. Prefer deep modules, information hiding, and pulling complexity into the server rather than pushing it onto the model. Do not replace production. The owner chooses a stack after reviewing the results.

## Workspace and inputs

- Use the `v2-design` worktree and branch. Its name predates the newer naming convention and remains until merge. Stop if started elsewhere.
- Before starting, check herdr agents and running processes. Only one session may work here. Stop and identify any competing harness.
- Commit and push only to `v2-design`. Do not modify main or another worktree, including bootstrap, extension, security, legacy worktrees, or dotfiles worktrees.
- Originally no PR was authorized. The later closeout instruction explicitly authorizes a small PR to main containing this brief only; it does not authorize implementing or merging v2.
- Put future deliverables under `v2/docs`, `v2/bench`, and `v2/prototypes/<stack>`. No extension code here. This archival brief is the explicit exception.
- Use managed scratch outside the repository, under the project's scratch root. The original brief named `~/Code/.scratch/personal-context-mcp/v2-design/`; follow the global managed-run rules when allocating new scratch.
- Read global and repository agent instructions, `docs/architecture.md`, and the improvement note before work.
- The real source is this rebuilt worktree. Do not read the stale `~/Code/personal-context-mcp/main` checkout, which has uncommitted changes.
- Find the Obsidian vault and read `20-Projects/personal-context-mcp/personal-context-mcp-improvement-brief-2026-10-13.md` without editing it. Ask if absent.
- Use the observed bootstrap facts below now. Read `~/Code/.scratch/personal-context-mcp/context-bootstrap/report.md` when it appears, but do not wait for it.
- Read `~/Code/.scratch/personal-context-mcp/browser-extension/design.md` and its sibling `report.md` when available. Reuse findings rather than repeating their work.
- If connected, call `context_bootstrap` for project `personal-context-mcp` to retrieve the latest decisions. If this harness lacks Personal Context MCP, proceed without it and list the missing context in the decisions for the owner.

## Safety

- Credentials come only from 1Password through `op run --env-file=deploy/admin.env.op -- ...`. Never print a DSN, key, or token.
- Production is read-only: SELECT, EXPLAIN, and audit-log reads only. No hosting, database administration, DNS, or firewall changes.
- Run benchmarks on local or test databases with synthetic production-scale data: about 5,500 conversations, 300,000 messages, 800 memories, 60 decisions, 25 notes, 7 lists, and 1,300 artifacts. Never copy production rows off the server.
- Never write to the real vault. Prototype against a scratch test vault.
- Never send real email. Use dry run or a test inbox.
- Ask before deleting data, adding a paid service, or first exposing any machine to the internet.

## Decisions already made

1. **Single user.** One owner only. A later session suggested open-sourcing the finished project; confirmation is still needed. Keep personal data and secrets out of the repo and avoid barriers to publishing.
2. **Transcript is primary.** Full transcripts are the session record. Background work derives summaries, decisions, tasks, and open questions. Keep Jev (TypeSafe): classify every new transcript, then use a distilling model only when Jev marks it worthwhile.
3. **Handoff first.** A new chat receives a small, fast project handoff: latest summary, open questions, active decisions, and open tasks. Historical transcript search is separate and explicit. The bootstrap fix's tests and before/after output are the floor for `start()`: handoff first; project inferred from the message with the reason stated; superseded decisions flagged; provenance on every item; layers run and skipped disclosed.
4. **Speed and reliability.** Tool-call speed decides between stacks. Capture reliability is a hard requirement, not a tradeoff. Development time, effort, and cost are not criteria. Report monthly running cost as information only and ask before a new paid service.
5. **Languages.** Python, Go, TypeScript, and Rust are allowed.
6. **Hosts.** Compare the always-on MacBook Pro, Helsinki Hetzner VPS, tuned Vercel, and a managed US host or edge platform. Trust the MacBook as a possible server; its macOS version alone does not disqualify it.
7. **Data.** Data may live in an owner-chosen cloud. Transcripts may go to Jev and one distilling model after secret removal.
8. **Vault.** Database sessions and transcripts remain canonical. The vault holds curated notes linking back to them. Layout: `Home.md`, `projects/{project}/`, `ideas/`, `setup/{tool}/`, `lessons/{course}/`, `concepts/`, `other/`, and `_archive/`. Folders describe note kind; tags carry `project/{slug}` and `status/{state}`. Confirm the proposed `concepts/` addition. Setup notes include editor, networking, credentials, orchestration, validation, and review tools. One lesson log corresponds to one lesson-axi browser run on localhost. Concept notes are separate, linked from projects and lessons, with spaced repetition. Ideas move to projects on commitment. The MCP owns only marked blocks; outside text is never overwritten. Explain whether v2 keeps the currently polled MBP worker model, observed about two hours stale on 2026-10-17.
9. **Extension ownership.** Its session owns save-on-click, automatic capture, backfill, side panel, and proof of no chat-app slowdown. v2 owns the plain HTTPS ingestion API. Configuration alone must switch the extension to v2 at cutover.
10. **Email.** AgentMail sends exactly what the owner requests, including an explicitly requested scheduled send, and nothing autonomously.
11. **Global policy.** Follow global agent instructions, worktree naming and merge routine, engineering principles, and committed agent instructions for real projects.

## Starting facts to verify when relied on

- Server: Python 3.12, FastMCP in Starlette, stateless Streamable HTTP, asyncpg, Supabase Postgres with pgvector. Vercel Python function without a pinned region; 48 tools. OAuth 2.1 uses Google sign-in via FastMCP's OAuth proxy, dynamic registration, and Postgres `oauth_kv` tokens.
- Reported timings: small `context_search` about 3 seconds from a chat app; Python CPU 1 to 2 ms/call; bootstrap 14 sequential queries; checkpoint 29 queries and a Jev wait with 12-second timeout; cold start about 2 seconds. These are prior observations, not measurements made here.
- On 2026-10-17 bootstrap ran only `project_state`, omitted session handoff, used 287/1,200 tokens, and returned an old decision without flagging its replacement. Many sessions had synthetic conversation IDs and null projects. A checkpoint with no inferred project went to review as `ambiguous_destination`.
- Search uses all-word `websearch_to_tsquery`, so long queries can find nothing. Embeddings are off.
- Chat-app servers are mostly in the US. Helsinki adds roughly 120 ms from the owner's US location. Measure from vendor-cloud geography rather than relying on the owner's browser.
- MacBook: 2016 Intel i5, 8 GB RAM, macOS 13.7.8, always on and on Tailscale. It also runs firstmate and several harnesses under herdr. Measure actual memory headroom before selecting it.
- Hetzner: Ubuntu 24.04, 8 GB RAM, about 80 GB disk, Helsinki, on the tailnet, already running a firstmate secondmate.
- Outside servers cannot read chat-app conversations. Capture requires model tools, browser extension, export import, or paste.
- Passing a large transcript as a tool argument makes the model generate it again. Large text must reach storage without passing through the model.

## Phase 0: understand the current system

Deliver `v2/docs/00-current-system.md`.

1. Trace connector addition through OAuth discovery, registration, Google sign-in, token issue, MCP initialize, tools/list, tool execution, and saved session. Explain database activity and timings at each stage. Include a Mermaid sequence diagram.
2. Inventory every tool: purpose, tools/list token cost, calls by app in the last 30 days from `audit_log`, and keep/merge/drop recommendation.
3. Measure the existing server with the phase 3 harness as the baseline.
4. Review shallow modules, pass-throughs, information leakage, removable special cases, temporal decomposition, and complexity pushed onto callers. Cite files and functions.

## Phase 1: design the interface

Deliver `v2/docs/01-interface.md`. Design and compare two tool surfaces. Aim for a few deep tools; improve on this starting point:

- `start(project?, hint?)`: session key plus capped latest handoff, meeting decision 3.
- `save(session_key, title?, notes?)`: persist the model's selected material and return immediately; classify and distill later.
- `find(query, keywords?, scope?)`: distilled items first, then transcripts, with explainable ranking.
- `lists(action, list, items?)`: read and change real lists through one tool.
- `ingest_link(url)`: server fetches and stores a share-link transcript; model supplies only a URL.
- `email(to, subject, body, attachments?, send_at?)`: exact requested AgentMail send; make unrequested sending difficult.

Specify:

- Plain HTTPS ingestion for extension and paste, separate from MCP: chunked, resumable, gzip, idempotent by app/chat/message index or content hash, safe to retry forever. Read the extension design first; match it or enumerate changes on both sides.
- A small server-hosted paste page: long chat, app, and date, no model involved.
- Session-key creation and carriage across saves; linking tool sessions to extension captures; attaching later exports without duplication, including existing `synthetic:` sessions.
- Unknown projects create suggestions rather than failures. Repeated saves update instead of duplicating.
- Total tools/list surface below 2,000 tokens.
- Whether Claude/ChatGPT workflow skills in `skills/` remain useful with simpler tools.

## Phase 2: stack proposals

Deliver `v2/docs/02-stacks.md`. At least four complete proposals, each naming language/SDK, web framework, sign-in, database/search, job runner, host, public HTTPS, and extension/vault connectivity. Give expected US-cloud call latency, cold start, memory, operational burden, failure modes, and informational monthly cost.

Required candidates:

- **A: tuned Python.** FastMCP, fewer round trips, always-on uvicorn on the MacBook through Cloudflare Tunnel or a US host. Compare local and Supabase Postgres.
- **B: Go.** Official Go MCP SDK, pgx, concurrent queries, single MacBook binary. Explain OAuth 2.1, dynamic registration, and Google sign-in via a library or separate auth service.
- **C: TypeScript/Workers.** Cloudflare MCP and OAuth libraries, Durable Objects SQLite or D1, Queues. Verify current storage, CPU, and full-text-search limits. Same language as the extension is useful context, not a performance result.
- **D: Rust.** Official rmcp, axum, sqlx, MacBook or Hetzner.
- **E: optional better idea.** Include if stronger than the required options.

Compare Postgres/pgvector, SQLite/FTS5/sqlite-vec, libSQL/Turso, and D1. Keyword ranking is required: BM25 or OR-based ranked tsvector. Vectors are optional and must earn their place through measurement.

Compare Cloudflare Tunnel, Tailscale Funnel, and Caddy with a Hetzner domain. Verify OAuth redirects and connector settings in ChatGPT, Claude, Gemini, and Grok, including phone apps. Test whether stateful Streamable HTTP gives a usable per-chat session ID in any app. Do not assert compatibility without testing.

## Phase 3: tests and benchmarks

Deliver `v2/bench` and `v2/docs/03-testing.md`. One repeatable command per stack. Explain the selected tools.

- Script real Streamable HTTP calls using an official Python or TypeScript MCP client; use Inspector CLI for spot checks. Report p50/p95/p99, cold/warm, for start/save/find/lists/email dry run.
- Use k6 or oha for four apps saving while extension backfill runs; hyperfine for individual commands where useful.
- Split database, network, and external-call time with OpenTelemetry or Server-Timing. Profile with py-spy, pprof, Go tracer, or equivalent.
- EXPLAIN ANALYZE every hot-path query in the test environment; pgbench or custom write load. Respect production's SELECT/EXPLAIN-only rule.
- Measure US-cloud RTT to each host using an approved tiny VM or US-east CI runner. Do not provision a paid service without permission.
- Retrieval: about 30 real questions with expected items and recall@5. Keep private questions and expected personal items out of the repo.
- Correctness: every tool contract, ported bootstrap tests, replayed-write idempotency, and byte-preservation outside vault markers.
- Document local Docker Compose or pgserver setup/teardown, candidate-host staging, and untouched production.

## Phase 4: extension integration

Deliver `v2/docs/04-extension.md`; no extension implementation here.

- Read its design and report. Map its ingestion requests onto v2 and list every required change on either side.
- Document the configuration-only cutover and rollback to the current server.
- Show how its side panel obtains the start handoff.
- Explain phone capture gaps and what save can and cannot preserve.

## Phase 4B: vault reader

Deliver `v2/docs/04b-vault.md` and prototype only against a scratch test vault.

- Map database records to curated note kinds and folders.
- Define marker syntax, preserved outside edits, and handling of moved notes.
- Define project/status tags, Home MOC, and folder indexes.
- Separate concepts from lesson logs; define lesson-axi input, spaced-repetition format, and progress views.
- Define idea promotion and finished/dead-item archival.
- Select queue/worker, sync, or direct delivery to the MBP. Report real lag honestly, including failures and conflicts.

## Phase 5: top-two prototypes

Under `v2/prototypes/<stack>`, build the two strongest phase 2 proposals far enough for fair comparison. Each implements start, save, find, email dry run, ingestion, and vault writing, plus sign-in sufficient for one real chat app. Include lists in the harness contract. Do not stop early to save effort. Run the full harness against both and the current server, keeping production read-only.

## Final deliverable

Write `v2/docs/05-recommendation.md`, then reply to the owner:

1. Answer-first paragraph naming the recommended stack, host, database, and connection, with plain reasons.
2. Measured current-server and prototype latency table: per-tool p50/p95, cold/warm.
3. Comparison of every proposal.
4. Direct answer, with numbers, on whether Go, Rust, or TypeScript was measurably faster than tuned Python. Clearly distinguish untested candidates.
5. Extension matches, changes, cutover switch, and rollback.
6. One-page vault design with a sample of each note type.
7. Migration for transcripts, distilled items, vault notes, and app connectors. Keep the current server live until cutover and document rollback.
8. Numbered owner decisions with suggested answers, including publication, concepts folder, approvals, missing MCP context, and any unverified app behavior.

Keep prose short and plain. Put answers first, comparisons in tables, and flows in Mermaid. At completion call `session_checkpoint` with the actual harness provider if available; otherwise explicitly report its absence.
