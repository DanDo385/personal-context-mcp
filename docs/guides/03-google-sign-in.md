# 3. Create your Google sign-in

When you add your server to ChatGPT, Claude, Gemini or Grok, the app opens a "Sign in with Google"
window. That's how your server knows it's really you. For this you create a free **Google sign-in
app** (Google calls it an *OAuth client*). It takes about ten minutes, costs nothing, and doesn't need
a credit card.

> **Which Google account?** Use the Google account you'll sign in with, ideally the same one you use
> for your AI apps. Only the email address(es) you give the setup wizard are ever let in. Anyone else
> who finds your server's address gets turned away, even if they sign in with Google successfully.

> **Decide your server's address first.** Google needs to know it. It's either
> `https://<project-name>.vercel.app` (free, from [guide 5](05-put-it-online.md); `personal-context-mcp`
> is the project name used there) or your own domain, such as `https://memory.yourname.com`. You can
> add more addresses later.

## Step 1: Create a Google Cloud project

1. Go to <https://console.cloud.google.com/projectcreate> and sign in.
2. **Project name:** `personal-context-mcp` (anything is fine).
3. Click **Create**, wait a few seconds, and make sure the new project is selected in the drop-down at
   the top of the page.

## Step 2: Describe your sign-in screen

1. Open <https://console.cloud.google.com/auth/branding> (menu: **Google Auth Platform → Branding**).
   If it asks you to **Get started**, click it.
2. **App name:** `Personal Context`. **User support email:** your email.
3. **Audience:** choose **External**. (*Internal* only exists for company Google accounts and would
   lock out personal Gmail addresses.)
4. **Contact information:** your email. Agree to the policy and click **Create**.

> **Google's menus move around every few months.** If the path above doesn't match what you see,
> search the page for "branding", "OAuth consent screen", or "audience". The fields you need are the
> same regardless of where Google puts them.

## Step 3: Publish it (important)

1. Open **Google Auth Platform → Audience**.
2. Under *Publishing status*, click **Publish app** and confirm.

Why: while the app is in *Testing*, Google makes every sign-in expire after 7 days, and you'd have to
reconnect every AI app weekly. Publishing needs **no review from Google** here, because your server
only asks for your name and email address.

## Step 4: Say what it's allowed to see

1. Open **Google Auth Platform → Data access** and click **Add or remove scopes**.
2. Tick `openid`, `.../auth/userinfo.email` and `.../auth/userinfo.profile`. Nothing else.
3. Click **Update**, then **Save**.

## Step 5: Create the client

1. Open **Google Auth Platform → Clients** and click **Create client**.
2. **Application type:** *Web application*. **Name:** `personal-context-mcp`.
3. Under **Authorised redirect URIs**, click **Add URI** and enter your server's address followed by
   `/auth/callback`, for example:

   ```
   https://personal-context-mcp.vercel.app/auth/callback
   ```

   It must match exactly: `https`, no trailing slash, and `/auth/callback` at the end.
4. Click **Create**.

✅ A box shows your **Client ID** (ends in `.apps.googleusercontent.com`) and **Client secret** (starts
with `GOCSPX-`). Copy both now. You can see the ID again later, but newer Google consoles only show the
secret once. If you lose it, just add a new secret to the same client.

> **If you can't find where to copy the secret:** Google sometimes hides it behind a "Show" button
> or buries it in a separate tab after creation. Look for "Client secret" or "Additional information".
> If the box disappeared, go to **Clients** → click your client's name → **Client secret** → you may
> need to create a new one.

## Where to put them

- **Using the setup wizard:** paste them when `uv run personal-context-mcp init` asks (choose
  `online`). Or, if you already have a `.env.local`, open it in a text editor and fill in
  `PC_OAUTH_CLIENT_ID=` and `PC_OAUTH_CLIENT_SECRET=`.
- **Using 1Password:** see [guide 7](07-keep-secrets-in-1password.md).

**Never** put the secret in a chat, a screenshot, an email or a GitHub issue.

## Common problems

| You see | Fix |
| --- | --- |
| `Error 400: redirect_uri_mismatch` when connecting an app | The redirect URI in step 5 doesn't exactly match your server's address + `/auth/callback`. Fix it in **Clients**; changes take a minute or two to apply. |
| `Access blocked: ... has not completed the Google verification process` | The app is still in *Testing*. Do step 3. |
| `Error 403: org_internal` | The audience is *Internal*. Create a new project with *External*. |
| Signed in fine, then the server says you're not allowed | The Google account you chose isn't in `PC_ALLOWED_SUBJECTS`. Add its email there (and to Vercel, guide 5). |

**Next:** [4. Create your database](04-database.md)
