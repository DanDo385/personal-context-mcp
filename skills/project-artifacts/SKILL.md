---
name: project-artifacts
description: >
  Handle multi-file project artifacts - web apps, code projects, document sets
  - so they stay one versioned artifact with their directory tree intact.
  Use whenever a produced artifact spans more than one file.
---

# Project artifacts

## Trigger

An artifact with more than one file: an HTML/CSS/JS page, a Vite or React app,
a small repo, a notebook with data files, a document plus its figures.

## Required tools

`artifact_save_bundle`, `artifact_create_revision`, `artifact_get`,
`artifact_manifest`, `artifact_attach_to_project`, `artifact_search`,
`artifact_queue_for_vault`

## Procedure

1. **One artifact, one call.** Send the whole tree to `artifact_save_bundle`
   as `{relative_path: contents}`. The structure is preserved exactly:

   ```
   index.html
   styles.css
   src/main.js
   public/diagram.svg
   ```

   becomes one artifact with four files, one id, one manifest, per-file
   hashes, and a storage prefix that mirrors the tree.

2. **Keep framework structure intact.** For a Vite or React project, include
   `package.json`, `vite.config.*`, `src/` and `public/` at their real paths.
   Flattening them makes the artifact unusable.

3. **Revise, don't duplicate.** When the user asks for changes, call
   `artifact_create_revision` with the complete new file set. That bumps the
   version, links it to its predecessor, and keeps the old version intact.
   Saving a second bundle instead leaves two artifacts with no relationship.

4. **Attach it to its project** with `artifact_attach_to_project` if the
   classifier did not, so it appears on the project's page.

5. **Offer the vault.** `artifact_queue_for_vault` queues a README note plus
   mirrors of files under the size threshold. Large media stays in cloud
   storage with a link. The write happens when the user's local sync worker
   next runs - say "queued", not "written".

## Safeguards

- Never call `artifact_save_text` per file for one project.
- Never rename files to flatten a tree.
- If a ZIP is supplied, keep the original and let the server extract it
  safely; do not unpack it yourself and send loose files.
- Check `artifact_manifest` after saving something large - it is the record of
  what was genuinely captured.

## Outputs

Report the artifact id, version, file count and total size, and say where it
landed - which project, and whether it was queued for the vault.

## Example

> "Build me an interactive explainer for Attention Is All You Need."

```
artifact_save_bundle(
  title="Attention Explainer",
  description="Interactive walkthrough of scaled dot-product attention.",
  project="attention-is-all-you-need",
  files={
    "index.html": "...",
    "styles.css": "...",
    "app.js": "...",
    "assets/attention-head.svg": "..."
  }
)
```

Later: "make the diagram bigger" → `artifact_create_revision` on the same
artifact, not a new bundle.
