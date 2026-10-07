# Troubleshooting

Start here: **`uv run personal-context-mcp check`** tests your settings, database, storage and
classifier, and lists **every** problem it finds at once, in plain words. Most answers below begin
with it.

Find what you're seeing below. Each guide also ends with a table for its own step.

## Installing

| You see | What it means | Fix |
| --- | --- | --- |
| `command not found: uv` | The Terminal hasn't noticed the new program. | Close the Terminal window, open a new one, try again. |
| `command not found: personal-context-mcp` | You ran it without `uv run`, or outside the project folder. | `cd ~/personal-context-mcp`, then `uv run personal-context-mcp ...`. |
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. | Open the Docker Desktop app, wait for it to say *running*, try again. |

## Settings

| You see | What it means | Fix |
| --- | --- | --- |
| `.env.local already exists` from `init` | The wizard won't overwrite your settings by accident. | Rename the old file (`mv .env.local .env.old`) or run `init --force`. |
| `Refusing to start. Fix the configuration problems above` | A setting is missing or unsafe. | The lines above it say exactly which; `check` shows them too. |
| `PC_AUTH_MODE=static is refused when PC_ENVIRONMENT=prod` | Test settings are being used online. | Run `init` again and choose `online`. |
| `PC_ALLOWED_SUBJECTS must list the account(s)...` | No email is allowed in. | Add your email to `PC_ALLOWED_SUBJECTS` in `.env.local`. |

## Database

| You see | What it means | Fix |
| --- | --- | --- |
| `database: cannot connect (InvalidPasswordError)` | The password in the connection string is wrong. | Reset it in Supabase (**Project Settings → Database**) to letters and numbers only, update `PC_DATABASE_URL`. |
| `database: cannot connect (gaierror / timeout)` | The address can't be reached. | Use Supabase's **Transaction pooler** string, not *Direct connection*. |
| `N pending migrations` | New database changes haven't been applied. | `uv run personal-context-mcp migrate` (always safe). |
| Everything stopped working after a week of not using it | Supabase paused the free project. | Resume it in the Supabase dashboard. |

## Online (Vercel)

| You see | What it means | Fix |
| --- | --- | --- |
| A Vercel login page at `/healthz` | Vercel's own protection is on. | `vercel project protection disable personal-context-mcp --sso` |
| `500` / *Serverless Function has crashed* | The server refused its settings. | `check` locally; fix `.env.local`; `deploy/vercel-env.sh .env.local`; `vercel deploy --prod`. The **Logs** tab on vercel.com shows the reason. |
| Build error mentioning `README.md` | An old copy of the project. | `git pull`, then deploy again. |

## Connecting the AI apps

| You see | What it means | Fix |
| --- | --- | --- |
| `redirect_uri_mismatch` | Google doesn't know your server's address. | Add `https://<your-address>/auth/callback` to the Google client ([guide 3](guides/03-google-sign-in.md)). |
| *has not completed the Google verification process* | The Google sign-in app is still in Testing. | Publish it ([guide 3](guides/03-google-sign-in.md), step 3). |
| Sign-in succeeds, then *access denied* | That Google account isn't allowed. | Add its email to `PC_ALLOWED_SUBJECTS`, push settings, redeploy. |
| Connected, but no tools show up | The app didn't finish setting up the connector, or the server isn't answering the way apps expect. | Run `uv run personal-context-mcp probe https://<your-address>/mcp`: any `FAIL` line names the broken step. If all checks pass, remove and re-add the connector and reload the page. |
| One app has fewer tools than the others | `PC_CLIENT_SCOPE_POLICY` grants that app fewer permissions; an app that matches no entry gets read-only tools. | Grant it more (`{"*":["*"]}` gives every app everything), push settings, redeploy, then remove and re-add the connector. |
| *Invalid or expired consent token*, or Approve leaves you on the `/consent` page | The approval page is single-use: it was already submitted, left open over 15 minutes, or (before the fix in this version) the browser blocked the hand-off to Google. | Close the sign-in window, then click **Connect** in the app again for a fresh page; click Approve once. If it persists, run `git pull` and redeploy. |
| Worked for a week, then asks to sign in again | The Google app is in Testing (7-day limit). | Publish it. |
| Gemini's *Custom apps* option or the connector disappeared | *Gemini Apps Activity / Keep Activity* was turned off. | Turn it back on; custom apps need it. You can still turn off *past chats*. |
| The app never uses its memory unprompted | The custom instruction is missing. | Paste [instructions/shared.md](../instructions/shared.md) into the app's settings. |

## Obsidian

| You see | What it means | Fix |
| --- | --- | --- |
| `Vault directory does not exist` | The folder in `OBSIDIAN_SYNC_VAULT_PATH` isn't there. | Create it or correct the path. |
| `worker rejected: check OBSIDIAN_SYNC_API_KEY` | Helper and server disagree on the shared key. | Make `OBSIDIAN_SYNC_API_KEY` equal `PC_WORKER_API_KEY`; push settings and redeploy if you changed the server's. |
| An edit of mine didn't show up in the apps | The helper wasn't running when you edited. | Start it; changed notes are pushed on its next pass. If the server also changed that note, the server's update is held back as a *conflict* and your edit stays in the vault. |

## Gemini digest

See the table at the end of [guide 11](guides/11-gemini-without-custom-apps.md#common-problems).

## Importing

| You see | What it means | Fix |
| --- | --- | --- |
| `Could not identify which provider produced this archive` | Unknown file layout. | Name the app: `import gemini file.zip`. For Gemini, export **JSON**, not HTML. |
| Many `classifier_degraded` | TypeSafe was unreachable. | Nothing is lost. `uv run personal-context-mcp reclassify` later. |

## Still stuck?

Open an issue using the **Setup help** template on GitHub. Include what you ran, what you expected and
what you saw, plus the output of `check`. **Never include your `.env.local` or any secret**; `check` doesn't
print any.
