# Glossary

Plain-language meanings for the words used in these docs.

| Word | Meaning |
| --- | --- |
| **API key** | A long password a program uses to prove who it is to a service, such as TypeSafe or Anthropic. Keep it secret like a password. |
| **Artifact** | A file an AI made or you saved: a chart, a PDF, a folder of code. |
| **Bucket** | A storage folder in Supabase where your saved files go. Yours are private. |
| **Checkpoint** | When an AI app saves everything useful from a conversation (decisions, facts, tasks) in one step. |
| **CLI / command** | Something you type in the Terminal, like `uv run personal-context-mcp check`. |
| **Connection string** | The address of your database, including its password: `postgresql://...`. Secret. |
| **Connector / custom app** | An outside tool you add to an AI app. Your server is one. |
| **Deploy** | Put your server on the internet (here: on Vercel). |
| **Environment (1Password)** | A named set of settings for one project, kept in 1Password instead of a file. |
| **`.env.local` file** | The text file holding *your* settings (`NAME=value` per line) in the project folder. Holds secrets; never share or upload it. Start from `.env.local.example`, or let `init` write it. |
| **Export** | A download of your data from an app, usually a `.zip`. |
| **Git / GitHub / clone** | Git downloads and tracks code; GitHub hosts it; "clone" means download a copy. |
| **Jev (TypeSafe)** | An AI service built for answering yes/no and multiple-choice questions very cheaply. Used to sort your old chats and decide which notes to link. It doesn't write text. |
| **Map of Content (MOC) / map** | An Obsidian page that lists and groups all notes about one topic. |
| **MCP** | Model Context Protocol: the standard that lets ChatGPT, Claude, Gemini and Grok use outside tools like your server. |
| **Migration / migrate** | Creating or updating the tables in your database. Always safe to run again. |
| **OAuth / sign-in app / client ID and secret** | The machinery behind "Sign in with Google". The client ID is public-ish; the client secret is secret. |
| **Obsidian / vault** | A notes app, and the folder of notes it opens. |
| **Orphan note** | A note with no links to or from any other note. |
| **`op` / 1Password CLI** | 1Password's Terminal command, which can supply secrets to other commands without saving them in files. |
| **Postgres / PostgreSQL / pgvector** | The database software Supabase runs, and an add-on for searching by meaning. |
| **Redirect URI** | The address Google sends you back to after signing in: your server's address + `/auth/callback`. |
| **Sensitivity** | How private something is: *normal*, *private* or *highly sensitive*. Highly sensitive content never leaves your computer or database. |
| **Server** | The program that answers the AI apps. Yours runs on Vercel. |
| **Service account (1Password)** | A special 1Password login for background programs, limited to the vaults you choose. |
| **Service role key (Supabase)** | The master key to your Supabase project. Only your server should have it. |
| **Skill** | A packaged set of instructions some AI apps can load to do a task well. |
| **Supabase** | A hosting service for your database and files. |
| **Terminal** | The app where you type commands. |
| **Token** | A temporary password proving you're signed in. |
| **uv** | The tool that installs and runs this project and its Python. |
| **Vercel** | A hosting service that puts your server on the internet. |
| **Worker / helper (`obsidian-sync`)** | The small program on your computer that writes notes into Obsidian. |
