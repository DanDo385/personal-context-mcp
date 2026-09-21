---
name: capture-artifacts
description: >
  Capture files - generated code, documents, images, datasets, multi-file
  projects - into durable shared storage. Use whenever a file worth keeping
  appears. Critically, distinguishes actually capturing bytes from merely
  recording that a file existed.
---

# Capture artifacts

## Trigger

- you generated code, a document, a diagram or a dataset the user should keep
- the user uploaded a meaningful file
- an image, video or audio file was created or supplied
- a multi-file project was produced

## The rule that matters

**Never tell the user something was saved unless the tool result says
`capture_status: "captured"`.**

You frequently *see* a file - an uploaded image, a canvas document - that your
platform does not hand to tools as bytes. In that case you do not have the
file. Recording that it existed is useful and honest; claiming it was archived
is neither.

## Required tools

`artifact_save_text`, `artifact_save_bundle`, `artifact_save_binary`,
`artifact_begin_upload` + `artifact_complete_upload`,
`artifact_register_reference`, `artifact_create_revision`,
`artifact_attach_to_project`, `artifact_search`, `artifact_signed_download`

## Procedure

1. **Decide whether you actually hold the contents.**
   - Text or code you generated: yes.
   - A file whose contents were provided to you as tool-readable content: yes.
   - An uploaded image you can describe but not read as bytes: **no**.
   - A file the user referred to by name: **no**.

2. **If you hold it, pick the right tool:**

   | Situation | Tool |
   | --- | --- |
   | one text or code file | `artifact_save_text` |
   | several files of one project | `artifact_save_bundle` (ONE call) |
   | an update to something already saved | `artifact_create_revision` |
   | a small binary you genuinely hold | `artifact_save_binary` |
   | anything large | `artifact_begin_upload` → PUT → `artifact_complete_upload` |

3. **If you do not hold it**, call `artifact_register_reference` with
   everything you can observe: filename, type, size if shown, what it is, which
   conversation. It is stored with status `pending_import` so a later provider
   export can fill in the bytes.

4. **Report the status accurately.** The tool result tells you. Say
   "saved - 4 files, hashes verified" or "I recorded the details, but this app
   didn't give me the file contents, so the image itself wasn't captured."

## Safeguards

- Never reconstruct bytes you cannot read. A plausible-looking base64 string
  that is not the real file is worse than no file.
- Never call `artifact_save_text` repeatedly for the files of one project -
  that creates unrelated artifacts and destroys the structure. Use
  `artifact_save_bundle`.
- Paths in a bundle are relative. Leading slashes and `..` are stripped
  server-side, but do not rely on that: send clean relative paths.

## Failure behaviour

If capture fails, say so and offer the content inline so the user does not
lose it. Do not retry silently more than once.

## Examples

> You generate a three-file web page.

```
artifact_save_bundle(
  title="Transformer Explainer",
  files={"index.html": "...", "styles.css": "...", "app.js": "..."},
  project="attention-is-all-you-need"
)
```
→ one artifact, three files, manifest generated, hashes verified.

> The user uploads a photo and asks you to keep it.

```
artifact_register_reference(
  title="whiteboard-photo.jpg",
  artifact_type="image",
  source_type="uploaded_by_user",
  expected_files=[{"filename": "whiteboard-photo.jpg"}]
)
```
→ then tell them: "I've noted the photo against this conversation, but this app
doesn't pass uploaded files to tools, so the image itself isn't stored yet. It
will be picked up when you next import your export."
