# 1. Install the tools

You'll install three small programs. Each takes a minute or two. If you already have one, skip it.

> **How to use the commands on these pages.** Open your Terminal (on a Mac: ⌘-Space, type
> *Terminal*, Enter). Copy a command from a grey box, paste it into the Terminal, and press Enter.
> Wait until the Terminal is ready for the next command (you'll see the prompt, usually ending in
> `%` or `$`) before pasting the next one.

## Mac

### 1. Git: downloads this project

Type `git --version` and press Enter.

- If you see something like `git version 2.39.3`, you have it. Move on.
- If a window pops up offering to install "command line developer tools", click **Install** and wait
  for it to finish. That installs Git.

### 2. uv: runs the project (and installs Python for you)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then **close the Terminal window and open a new one**, so it notices the new program, and check:

```bash
uv --version
```

✅ You should see `uv 0.x.y`. If it says `command not found`, close and reopen the Terminal again.

### 3. Node.js: needed only for putting the server online with Vercel

Download the **LTS** installer from [nodejs.org](https://nodejs.org) and run it. Then:

```bash
npm install -g vercel
vercel --version
```

✅ You should see a version number.

## Linux

```bash
sudo apt install git curl          # Debian/Ubuntu; use your distribution's package manager otherwise
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install Node.js from [nodejs.org](https://nodejs.org) or your package manager, then
`npm install -g vercel`.

## Windows

Use **WSL** (Windows Subsystem for Linux), which gives you a Linux terminal inside Windows. Microsoft's
guide: [Install WSL](https://learn.microsoft.com/windows/wsl/install). Then follow the Linux steps above
*inside the WSL terminal*. The background helper for Obsidian ([guide 8](08-obsidian.md)) is easiest
on a Mac or Linux, but it also runs by hand in WSL.

## Download this project

```bash
cd ~
git clone https://github.com/DanDo385/personal-context-mcp.git
cd personal-context-mcp
uv sync
```

`uv sync` downloads everything the project needs, including the right version of Python. The first
time takes a minute.

✅ Check it worked:

```bash
uv run personal-context-mcp --help
```

You should see a list of commands (`init`, `serve`, `migrate`, `check`, `import`, ...).

> **Every later guide assumes you're inside this folder.** If you open a new Terminal, go back with
> `cd ~/personal-context-mcp` first.

## Optional: Docker, for trying it locally

[Guide 2](02-try-it-locally.md) can run a database on your computer with Docker Desktop
([download](https://www.docker.com/products/docker-desktop/)). It's optional: if you'd rather not
install Docker, guide 2 shows how to use your free Supabase database instead.

**Next:** [2. Try it on your computer](02-try-it-locally.md), or go straight to
[3. Create your Google sign-in](03-google-sign-in.md).
