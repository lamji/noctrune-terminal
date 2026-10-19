---
name: notes-feature
description: Notes are a Notion-style Agent view group after Terminals, saved as Markdown files outside the project
type: fact
keywords: notes, note-taking, notion, notespanel, paste, markdown, ctrl+shift+j
---

Notes is a tab group in the Agent view; its header button sits right after Terminals (`TAB_GROUPS` in `nocturne.py`), shortcut Ctrl+Shift+J.
Its side list works like the Agents and Explorer panels: search (matches contents), OPEN notes, then the project's other NOTES; clicking Notes again hides or shows it.
Notes are mostly pasted in (*Paste as note* reads the clipboard) but stay editable: a title above Markdown text, with **Copy** to get the text back out.
Each note is one file `# Title`, blank line, body, at `~/.config/nocturne/notes/<project path slug>/<YYYYmmdd-HHMMSS>.md`, never inside the project.
Notes autosave 500 ms after a change (temp file, then swapped in); a note closed empty deletes its file; open notes reopen after a restart.
Decided with the user on 2026-10-19.
Check: `.nocturne/reports/20261019-095222-notes-feature.md` (full flow trace)
