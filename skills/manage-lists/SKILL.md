---
name: manage-lists
description: >
  Work with the user's canonical lists - tasks, reading, shopping, people to
  follow up, ideas. Use whenever the user adds to, completes, moves, or asks
  about a list. Never answer a list question from memory or write a Markdown
  list instead.
---

# Manage lists

## Trigger

Anything of the shape "add X to my Y list", "what's on my Y list", "mark X
done", "move X from A to B", "remind me to Z". Also whenever you are about to
produce a bulleted list of things the user needs to remember - that belongs in
a real list, not in your reply.

## Required tools

`list_search`, `list_get`, `list_create`, `list_add_item`,
`list_complete_item`, `list_move_item`, `list_update_item`,
`list_archive_item`, `list_recent`, `list_find_item`

## Procedure

1. **`list_search` first, always.** The user keeps many lists and the name in
   conversation rarely matches the stored name: "my reading list" may be
   stored as "Books to read", "personal AI tasks" as "personal-ai-tasks".
   Searching costs one call; creating a duplicate splits a list in two and is
   tedious to undo.

2. **Add to the list you found** with `list_add_item`. Adding an item whose
   text matches an existing one updates that item rather than creating a
   second copy, so a repeat is harmless.

3. **Only create when nothing fits.** If `list_search` genuinely returns no
   reasonable home, call `list_create` - and tell the user you created a new
   list, so they can correct you.

4. **Use ids, not text, for changes.** `list_get` returns a stable `id` per
   item. Pass that to `list_complete_item`, `list_move_item` and
   `list_update_item`. Matching on text can complete the wrong item when two
   read similarly.

5. **Read before you answer.** "What's on my reading list?" means
   `list_get`, not recall. The list is canonical state and it changes from
   other apps.

## Safeguards

- Never invent list contents. If `list_get` returns nothing, the list is empty.
- Never delete. `list_archive_item` drops an item while keeping the row and
  its history.
- When the user's phrasing is ambiguous between two existing lists, ask which
  one rather than picking.

## Outputs

Confirm concretely: "Added 'Test Gemini connector on iPhone' to
personal-ai-tasks (now 7 open items)." Vague confirmations make it impossible
for the user to tell whether it landed on the right list.

## Failure behaviour

If the list does not exist and you are not confident enough to create one, say
which lists do exist and ask. If a completion finds no matching item, say so -
do not claim it was completed.

## Examples

> "Add Morgan Stanley to my networking follow-up list."

`list_search("networking")` → finds `networking-follow-up` →
`list_add_item(list="networking-follow-up", text="Morgan Stanley")`

> "Move the browser extension idea from someday to active."

`list_search("someday")`, `list_get("someday")` to get the item id,
`list_move_item(item_id=..., target_list="active")`

> "What books are on my reading list?"

`list_search("reading")` then `list_get`. Report what it returns, nothing more.
