# 4. Create your database

Everything your AI apps remember is kept in a database. The guides use [Supabase](https://supabase.com),
which gives you a proper PostgreSQL database plus file storage, free to start, in your own account.

> **One project per app.** If you already use Supabase for something else, still create a **new
> project** for this. Keeping them separate means one can never read or break the other.

## Step 1: Create the project

1. Sign up or sign in at <https://supabase.com/dashboard>.
2. Click **New project**.
3. **Name:** `personal-context-mcp`. **Region:** the one closest to you.
4. **Database password:** click **Generate a password**, then **copy it somewhere safe right now**
   (a password manager is ideal). You'll need it in step 2, and Supabase won't show it again.
5. Click **Create new project** and wait a minute or two while it starts.

> The free plan allows two active projects and pauses a project after a week with no activity. If
> you use your AI apps daily that won't happen, and a paused project can be resumed from the
> dashboard.

## Step 2: Copy the connection string

1. In your project, click **Connect** (top of the page).
2. Choose the **Transaction pooler** tab. It works both from your computer and from Vercel.
3. Copy the URI. It looks like:

   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:6543/postgres
   ```

4. Replace `[YOUR-PASSWORD]` (including the brackets) with the password from step 1.

> If your password contains characters like `@`, `#`, `/` or `%`, it can confuse the connection
> string. The easiest fix is to reset the database password (**Project Settings → Database**) to one
> made of only letters and numbers.
>
> **Safe characters for your database password:** letters (`a-z`, `A-Z`), numbers (`0-9`), hyphens (`-`), and underscores (`_`). Avoid `@ : / # ? % & +` and spaces — they break the connection string.

## Step 3: Copy the project URL and service key

These let the server store files (charts, PDFs, code you ask it to keep).

1. Open **Project Settings → API Keys** (or **Data API**).
2. Copy the **Project URL**, for example `https://abcdefghijklmnop.supabase.co`.
3. Copy the **service_role** key (sometimes shown as the *secret* key).

> The service role key can do anything in your project. It only ever goes in your server's settings:
> never in a website, an app, a chat or a GitHub issue.

## Step 4: Give them to the setup wizard

```bash
uv run personal-context-mcp init
```

Choose `online`, and paste the three values when asked:

| The wizard asks | Paste |
| --- | --- |
| Connection string | the URI from step 2 (with your password in it) |
| Project URL | the URL from step 3 |
| Service role key | the key from step 3 |

(It also asks for your server's address and the Google client from [guide 3](03-google-sign-in.md).
Both can be left blank and filled into `.env.local` later.)

## Step 5: Create the tables and check

```bash
uv run personal-context-mcp migrate
uv run personal-context-mcp check
```

✅ `check` should report `database: connected, schema up to date`, `storage: buckets present and
private`, and end with **All checks passed.** `check` also creates your two private storage folders
("buckets") in Supabase the first time.

You can look at your data any time in the Supabase dashboard under **Table Editor**, for example the
`memories`, `decisions` and `conversations` tables.

## Common problems

| You see | Fix |
| --- | --- |
| `database: cannot connect (InvalidPasswordError)` | The password in the connection string is wrong or still says `[YOUR-PASSWORD]`. |
| `database: cannot connect (gaierror)` or a timeout | Copy the **Transaction pooler** string, not *Direct connection*. The direct one needs IPv6, which many home networks lack. |
| `storage: ... bucket ... is public` | Someone made a bucket public. In **Storage**, make `personal-context-mcp` and `personal-context-mcp-archives` private. The server refuses to use a public bucket on purpose. |

**Next:** [5. Put it online](05-put-it-online.md)
