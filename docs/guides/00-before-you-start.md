# 0. Before you start

This page takes five minutes. It explains what you'll build, the handful of words the other guides
use, and the choices you'll make along the way. Nothing here asks you to type anything yet.

## What you'll end up with

```
 Your phone and browser                      The internet                        Your computer
 ──────────────────────                      ────────────                        ─────────────
 ChatGPT ─┐
 Claude  ─┼──► https://your-address ──► your server ──► your database          Obsidian notebook
 Gemini  ─┤         (sign in once)          │                                      ▲
 Grok    ─┘                                 └──── notes to write ──► helper ───────┘
```

- **Your server** is a small program running on a free hosting service (the guides use Vercel). It's
  the only thing the AI apps talk to.
- **Your database** (Supabase, free) holds everything: memories, decisions, projects, lists, your
  imported chat history, and files.
- **The helper** is an optional program on your own computer. It copies notes from the database into
  your Obsidian folder, and sends your edits back. It only ever *calls out* to your server; nothing on
  the internet can reach into your computer.

Once it's set up, your computer doesn't need to be on for the AI apps to work. It only needs to be on
for notes to arrive in Obsidian.

## Words worth knowing

You don't need to understand these deeply; the guides tell you exactly what to do. The
[Glossary](../glossary.md) has more.

| Word | Plain meaning |
| --- | --- |
| **MCP** | A standard way for AI apps to use outside tools. It's why ChatGPT, Claude, Gemini and Grok can all connect to the same server. |
| **Connector** | What the AI apps call an MCP server you add to them ("custom connector", "custom app"). |
| **Terminal** | The text window where you type commands. On a Mac: press ⌘-Space, type *Terminal*, press Enter. |
| **Repository / repo** | This project's folder of code, downloaded to your computer with `git clone`. |
| **`.env` file** | A small text file holding your settings and secrets. It stays on your computer and is never uploaded to GitHub. |
| **OAuth / "sign-in app"** | The "Sign in with Google" button. You create a free Google sign-in app so the AI apps can prove it's you. |
| **Deploy** | Put your server on the internet. |

## The three decisions you'll make

1. **Try it locally first, or go straight online?** Trying it on your computer first
   ([guide 2](02-try-it-locally.md)) takes ten minutes and proves everything works before you create
   any accounts. The phone apps can't reach a server on your computer, so you'll go online afterwards
   anyway.
2. **Where to keep your secrets.** The simple way is the `.env` file the setup wizard writes. If you
   already use 1Password, [guide 7](07-keep-secrets-in-1password.md) shows how to keep every secret
   there instead, so no password is ever stored in a file.
3. **Whether to import your old chats.** It's optional and can be done any time later
   ([guide 9](09-import-your-history.md)). The first sorting pass is cheap. The careful note-writing
   pass costs money, but it always shows an estimate first and only runs on the chats that are worth
   it.

## A promise about safety

- Nothing in these guides can delete your chats from ChatGPT, Claude, Gemini or Grok. Importing only
  *reads* the export files you download.
- Running any import or setup step twice is safe; nothing is duplicated.
- Your secrets never need to be pasted into a chat, an issue, or anywhere public. If a guide ever seems
  to ask for that, stop: it's a mistake in the guide, so please [report it](../../CONTRIBUTING.md).

**Next:** [1. Install the tools](01-install-the-tools.md)
