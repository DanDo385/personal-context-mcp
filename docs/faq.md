# Frequently asked questions

## The basics

**Do I have to stop using ChatGPT's (or Claude's...) own memory?**
No. The apps keep their built-in memory. personal-context-mcp is the shared layer underneath that all
of them can see, and the custom instruction tells each app to treat it as the main record.

**Does this work on my phone?**
Yes. You connect each app once on a computer, and the connection then works in that app on your phone.
Obsidian notes reach your phone if your vault is in iCloud (iPhone/iPad) or synced another way.

**Do I need to keep my computer on?**
Only for notes to arrive in Obsidian. The AI apps talk to your online server, not your computer.

**Can other people use my server?**
Only the Google account(s) listed in `PC_ALLOWED_SUBJECTS`. Someone who finds the address can't get in
even if they sign in with their own Google account.

**Can I share one server with my family?**
It's designed as *personal* memory: everyone allowed in sees the same memories. For separate people,
run separate servers; each takes about an hour to set up.

**Is it open source? Can I change it?**
Yes, under the [MIT license](../LICENSE). Contributions are welcome; see
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Privacy and money

**Who can see my data?**
You, and the services you put it on: your Supabase project, your Vercel account and your Obsidian
folder. The authors of this project can't see anything. Details:
[Privacy and costs](privacy-and-costs.md).

**What gets sent to TypeSafe or Anthropic?**
Only if you add their keys, and only what the sorting and close-reading steps need, with emails and
keys removed first. Anything screened *highly sensitive* (passwords, keys, ID or card numbers) is never
sent. You can keep more local with `PC_NO_EXTERNAL_SENSITIVITY=private,highly_sensitive`, or everything
with `PC_IMPORT_DEFAULT_SENSITIVITY=highly_sensitive`.

**How much does it cost?**
The hosting and database can be free. The optional sorting and close-reading steps are pay-as-you-go,
and close reading always shows an estimate first. See [Privacy and costs](privacy-and-costs.md).

## How it behaves

**Why does Gemini ask me to confirm every save?**
Gemini requires confirmation for every action that writes something. Claude and ChatGPT ask at first
and can be set to "always allow".

**I attached a file in the app. Is it saved?**
Most apps show attachments to the AI but don't pass the file itself to connectors, so the file is
recorded as "pending" and filled in when you import that app's export. Files the AI *creates* (charts,
code, documents) can be saved directly. The system never claims to have saved a file it didn't.

**Will importing the same export twice make duplicates?**
No. Conversations are recognised by their IDs and files by their contents.

**Can I delete something it remembered?**
Yes: ask any connected app to forget or retract it. The history of changes is kept, so a mistaken
deletion can be undone.

**What happens if TypeSafe or Anthropic is down?**
Nothing breaks. Sorting falls back to simple built-in rules and the affected items are queued to be
re-sorted; close reading simply waits until you run it again.

## Running it

**Can I run it somewhere other than Vercel?**
Yes. It's an ordinary Python web app with a `Dockerfile`: Cloud Run, Fly.io, Railway, a home server.
It needs a public **https** address. See [Architecture: Deployment](architecture.md#deployment).

**Can I use a different database than Supabase?**
Any recent PostgreSQL with the `pgvector` extension works for the data (the tests run on PostgreSQL 16; the Docker setup uses 17). File storage is Supabase Storage
or a local folder (`PC_STORAGE_BACKEND=local`), which suits a home server but not Vercel.

**How do I update to a new version?**
`git pull`, `uv sync`, `uv run personal-context-mcp migrate`, `vercel deploy --prod`.

**How do I back up my data?**
Your data is your Supabase database: Supabase keeps daily backups on paid plans, and you can export any
time. See [Architecture: Backup and recovery](architecture.md#backup-and-recovery).
