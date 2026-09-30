# 11. Gemini without custom apps (Google AI Plus or free)

Gemini can only connect to your server directly with **custom apps**, which need a **Google AI Pro or
Ultra** plan ([guide 6](06-connect-your-ai-apps.md)). On Plus or the free plan, use this instead:

- Once a week your Mac writes a **digest** of your Personal Context (who you are, how you like to
  work, your projects and decisions, open to-dos, recent conversations, open questions) into **one
  Google Doc** in your Drive.
- A **Gemini Gem** uses that Doc as knowledge, so chats with the Gem know your context.

It's one-way: Gemini can read your context but can't save new things to it. Anything marked private
or highly sensitive is left out of the Doc, because it goes to Google.

Time: about 15 minutes, once.

## Step 1: Let the project write to Google Drive

Use the same Google Cloud project as your sign-in app ([guide 3](03-google-sign-in.md)).

1. **Turn on the Drive API:** open
   <https://console.cloud.google.com/apis/library/drive.googleapis.com>, make sure your project is
   selected at the top, and click **Enable**.
2. **Allow the one permission it needs:** **Google Auth Platform → Data access → Add or remove
   scopes**, tick `.../auth/drive.file`, then **Update** and **Save**. `drive.file` only lets this app
   see and change **the files it creates itself**, never anything else in your Drive, and Google
   doesn't require a review for it.
3. **Create a second client, for your computer:** **Google Auth Platform → Clients → Create client**,
   choose **Desktop app**, name it `personal-context-mcp gemini sync`, and click **Create**. Copy the
   **Client ID** and **Client secret**.

## Step 2: Save the two values

**In `.env.local`** (the simple way):

```
PC_GOOGLE_DRIVE_CLIENT_ID=<the client ID>
PC_GOOGLE_DRIVE_CLIENT_SECRET=<the client secret>
```

**Or in 1Password:** add a section **Google Drive** with fields `client_id` and `client_secret` to your
deployment's item, and add the three `PC_GOOGLE_DRIVE_*` lines from
[`deploy/admin.env.op.example`](../../deploy/admin.env.op.example) to your `deploy/admin.env.op`.

## Step 3: Sign in once

```bash
uv run personal-context-mcp gemini-sync authorize
```

1Password users add `--op-item <item-id> --op-vault <vault>` and run it through
`op run --env-file=deploy/admin.env.op --`.

A browser tab opens. Choose **the Google account you use Gemini with**. Google shows *"Google hasn't
verified this app"*: that's your own app, so click **Continue**, then allow access to "the specific
Google Drive files that you use with this app".

✅ The terminal says `Saved to .env.local` (or to 1Password). The saved token is never shown.

## Step 4: Create the Doc

```bash
uv run personal-context-mcp gemini-sync push
```

✅ It prints `Created Personal Context (for Gemini): https://docs.google.com/document/d/...`. Open the
link to see what Gemini will know. (`gemini-sync push --dry-run` builds the digest without uploading
it, and `personal-context-mcp digest` prints it.)

Every later push replaces the text of **the same Doc**, so its link never changes.

## Step 5: Make a Gem that uses it

1. At <https://gemini.google.com>, open **Gems** (in the side menu, or **Explore Gems**) → **New Gem**.
2. **Name:** `Personal Context`.
3. **Instructions:**

   ```
   The attached document is my Personal Context: my shared memory across my AI apps,
   updated weekly. Use it as background about me, my projects, decisions and preferences.
   Prefer what I say in the conversation over it, and don't quote it back unless I ask.
   ```

4. **Knowledge:** click **+** → **Drive** → choose **Personal Context (for Gemini)**.
5. **Save.**

Start chats from this Gem (on the web or in the Gemini phone app) whenever your context matters.

## Step 6: Update it every week

On a Mac:

```bash
deploy/install-gemini-sync.sh
```

✅ It prints `Installed ... (weekly)`. It runs every Monday at 9:00, or when the Mac next wakes up
if it was asleep. To run it now: the `launchctl kickstart ...` line it printed. Change the day or
hour with `GEMINI_SYNC_WEEKDAY` (0 = Sunday) and `GEMINI_SYNC_HOUR` before installing.

Linux: run `uv run personal-context-mcp gemini-sync push` weekly from `cron` or a systemd timer.

## Common problems

| You see | Fix |
| --- | --- |
| `Set PC_GOOGLE_DRIVE_CLIENT_ID and PC_GOOGLE_DRIVE_CLIENT_SECRET first` | Do step 2, and check you're in the project folder. |
| `Access blocked: ... disabled` or `API has not been used in project` | The Drive API isn't enabled (step 1.1). |
| `Google refused the saved authorization` | The sign-in expired or was removed at myaccount.google.com. Run step 3 again. |
| Gemini answers with old information | Check the Doc's text is current (open its link). If the Doc is current but the Gem isn't, remove it from the Gem's knowledge and add it again. |
| I deleted the Doc | The next push creates a new one; attach the new Doc to the Gem. |
