# Privacy and costs

## Where your data lives

| What | Where | Who can see it |
| --- | --- | --- |
| Memories, decisions, projects, lists, imported conversations | Your Supabase database | You (and Supabase as your host) |
| Saved files (charts, PDFs, code) | Your Supabase Storage, in private buckets | You; the apps get short-lived download links |
| Your original export `.zip` files | A separate private bucket, optional | You |
| Notes | Your Obsidian folder, on your computer (and iCloud, if you put it there) | You |
| Your server's settings and secrets | Your `.env.local` file or 1Password, and Vercel's encrypted settings | You |

There's no central service run by the authors of this project, nothing is collected about you, and
there's no telemetry.

## Who can connect

Only the Google account(s) listed in `PC_ALLOWED_SUBJECTS`. The check happens on every request: a
valid Google sign-in from any other account is refused. Each AI app is also given only the
permissions you allow it (`PC_CLIENT_SCOPE_POLICY`; for example, you can make one app read-only).
Someone who merely finds your server's address learns nothing: every unauthenticated request gets the
same refusal.

## What leaves your database, and when

| Service | Only if | What it receives | What it never receives |
| --- | --- | --- | --- |
| **TypeSafe (Jev)** | you set `PC_TYPESAFE_API_KEY` | a short excerpt of a conversation or note, with emails, keys, tokens and long numbers removed | anything at a level in `PC_NO_EXTERNAL_SENSITIVITY` |
| **Anthropic (Claude)** | you set `PC_ANTHROPIC_API_KEY` **and** run `extract` | full transcripts of the conversations flagged as worth reading, with the same removals | the same; plus nothing runs until you ask |
| **OpenAI / Gemini embeddings** | you set `PC_EMBEDDING_PROVIDER` (off by default) | text to index for meaning-based search | the same |

### The privacy screen

Before anything from an imported chat goes anywhere, a check on your computer looks for:

- **Highly sensitive** (never sent anywhere): private keys, API keys and tokens, US Social Security
  numbers, payment card numbers, seed/recovery phrases, "my password is ...".
- **Private:** talk about health (diagnoses, medication, therapy), legal matters (lawyers, lawsuits,
  custody) and personal finances (salary, tax returns, account numbers).

It can only make a conversation *more* private, never less. The patterns are in
[`importers/sensitivity.py`](../src/personal_context_mcp/importers/sensitivity.py), in one short file
you can read.

### Three privacy settings

```
PC_NO_EXTERNAL_SENSITIVITY=highly_sensitive            # default
PC_NO_EXTERNAL_SENSITIVITY=private,highly_sensitive    # also keep health/legal/money local
PC_IMPORT_DEFAULT_SENSITIVITY=highly_sensitive         # keep your entire imported history local
```

## Costs

| Item | Typical cost | Notes |
| --- | --- | --- |
| Vercel hosting | Free | The Hobby plan is for personal, non-commercial use. [Pricing](https://vercel.com/pricing) |
| Supabase database and storage | Free to start | The free plan has size limits and pauses after a week of no use. [Pricing](https://supabase.com/pricing) |
| Google sign-in | Free | |
| Obsidian | Free | Obsidian Sync is optional and paid; iCloud works for Apple devices. |
| Your own domain | Optional | About $10–15 a year from any registrar. |
| TypeSafe (Jev) sorting | Pay as you go | Priced per question; one request per conversation asks all the sorting questions at once. Identical questions are answered from a cache instead of being paid for again. [Console](https://console.typesafe.ai) |
| Anthropic close reading | Pay as you go | See the example below. Always run `extract --dry-run` first. |

### A worked example for close reading

With the default model, Claude Opus 5.5 ($4 per million input tokens, $20 per million output tokens),
a typical conversation of about 20,000 characters costs roughly **5–7 cents** to read and summarise.
So 500 flagged conversations come to about **$30**. The cheaper `claude-sonnet-5` ($2 / $10) costs
about half that. Real costs vary with conversation length: the `--dry-run` estimate uses your actual
conversations.

Only conversations the sorting pass flagged as worth it are eligible, most chats (quick questions,
one-off lookups) never are, and you choose how many with `--limit`.
