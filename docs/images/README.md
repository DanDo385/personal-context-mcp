# Screenshots for the documentation

This directory holds screenshots referenced by the guides. Replace each placeholder below with an
actual PNG or JPEG (keep file sizes under 300 KB). Images here are committed to the repo and served on
GitHub.

## What to capture

### `google-oauth-branding.png`
The Google Cloud Console branding screen after clicking "Get started".
Show: App name "Personal Context" filled in, User support email field, Audience "External" selected.

### `google-oauth-redirect-uri.png`
The "Create client" page with the Authorised redirect URIs section visible.
Show: Application type "Web application", one redirect URI like
`https://personal-context-mcp.vercel.app/auth/callback` filled in, and the Client ID/Secret box
that appears after clicking Create.

### `supabase-connect.png`
The Supabase "Connect" dialog, Transaction pooler tab selected.
Show: the full connection string with `[YOUR-PASSWORD]` visible, and the copy button.

### `supabase-api-keys.png`
Supabase Project Settings → API Keys page.
Show: Project URL field, and the `service_role` key field (partially blurred for safety).

### `vercel-domains.png`
The Vercel project page, Domains tab.
Show: the `personal-context-mcp.vercel.app` address under "Production", and the Domains section.

## Usage in guides

In each guide, images are referenced as:

```markdown
![alt text](../images/filename.png)
```

Replace with actual screenshots when available. Guides currently describe the UI in text and remain
fully functional without images — screenshots are a supplement, not a requirement.