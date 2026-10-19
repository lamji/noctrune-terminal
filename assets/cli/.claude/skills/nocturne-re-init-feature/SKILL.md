---
name: nocturne-re-init-feature
description: Read back the flow reports saved under .nocturne/reports, and the project memory in .nocturne/memory, so their findings are in context again. Use after a compact or a cleared session, or when the user asks to build on a trace another session wrote. A new session does not need it.
disable-model-invocation: true
---

# Nocturne Re-init Feature

Bring the saved flow reports back into this conversation. The
`nocturne-init-feature` skill writes them; a compact, a cleared session, or
a new session loses what they said. After a compact, Nocturne's session
hook already loads this session's own reports again by itself.

Also bring back the project memory in `.nocturne/memory/` (one lasting project fact each). Start from
`.nocturne/index/CONTENTS.md` when it exists: its table of contents summarizes every entry and report (with
section line numbers) and its index lists each subject's places. Read the entries the request needs (all of
them when it names none and there are only a few), not the whole folder. Memory says where to look
and what is not true here; it is never evidence for a cause, and an entry naming a file or setting may be
stale, so check it still exists before relying on it.

This skill only reads. Do not change source code.
Do not edit, rewrite, or delete a report.

The reports are in the directory this session was started in. This skill's
own files ship inside the Nocturne app and are not part of the project:
never search or read the folder they live in. Finding and choosing reports
is internal; do not narrate it. Reply with what the reports say.

Everything after the command says which reports are wanted. It may be
empty.

## Which reports to read

The reports are markdown files under the directory this session was
started in:

    .nocturne/reports/<YYYYMMDD-HHMMSS>-<flow-slug>.md

Each opens with a frontmatter block: `id`, `session`, `title`, and
`updated` once it has been brought up to date.

1. List the folder and read only the frontmatter of every report. If the
   folder is missing or empty, say so and stop. In Claude, list and read
   with Glob and Read, not Bash (a Bash command stops to ask the user for
   permission). In Codex, use a plain shell command. Do not look for other
   tools or MCP resources.
2. Work out this session's id: when the request carries `Session: <id>`,
   use that id. Otherwise use `${CLAUDE_SESSION_ID}`.
3. Pick the reports:
   - **Nothing was asked for** (the request is empty, or says it was not
     given): do not ask what to load. Take every report whose `session` is
     this session's id. If none match, take the three newest reports.
   - **A report id or file name was given**: take that report.
   - **A topic or task was given**: take this session's reports that bear
     on it, then any other report whose title bears on it. Leave the rest
     unread.
4. Read each picked report in full, newest first.

## Using what they say

A report describes the code as it was when the report was written. Treat
it as a map of where to look, not as the code itself.

- Before relying on a claim, re-read the `file:line` it cites. If the code
  no longer matches, trust the code and say which report is out of date.
- A report whose `session` is not this session's id was written by other
  work: nothing in this conversation has checked it.
- An `Inferred Or Unresolved` entry is still unresolved. Do not present it
  as a finding.

## Reply

Keep it short:

1. One line per report read: its path, its title, and when it was written
   or last updated.
2. What the reports establish that matters for the request, in a few lines,
   with the `file:line` each point rests on.
3. Any report found out of date, and the claim that no longer holds.

If the request also asked for work, carry on with it now, using the
reports as the starting point.
