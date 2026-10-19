---
name: nocturne-init-feature
description: Trace a code flow end-to-end with file:line evidence, including which branches this environment runs, and save the report under .nocturne/reports. Use for "what calls what", login, checkout, API handler, button click, or background job questions, and before naming the cause of a wrong value or bug, so the answer rests on the trace instead of a guess.
disable-model-invocation: true
---

# Nocturne Init Feature

Build an evidence-backed trace of the requested flow and save it as a
markdown report in the project. This is investigation only: do not change
source code.

The project is the directory this session was started in. This skill's own
files ship inside the Nocturne app and are not part of the project: never
search, read, or trace the folder they live in.

Start investigating the flow straight away. Where the report goes, which
reports exist, the session id, and the frontmatter are internal: handle
them silently and do not narrate them. The user sees progress on the flow
itself, and the report's path once, in the final reply.

Everything after the command is the flow to document. If nothing was given,
ask which flow to trace before reading anything.

A request that starts with `update` is not a new flow: it brings reports
this session already wrote up to date. Follow "Updating a report" for it.

## Mandatory rules

1. Find the concrete trigger point before claiming the flow starts anywhere.
2. Do not assume behavior from filenames, route shapes, variable names,
   comments, UI labels, or memory of the repo. Read the code first.
3. Read each traced function, config, route, guard, resolver, helper, and
   consumer before summarizing it.
4. Do not skip wrappers, aliases, callbacks, middleware, hooks, class
   methods, imported indirections, or async continuations.
5. Trace the values that matter: parameters, locals, mutations, return
   values, thrown errors, side effects, generated URLs, and identifiers.
6. Cite `file:line` for every concrete claim, with paths relative to the
   project root.
7. When the flow generates a URL, token, slug, or identifier, state which
   actor or runtime context supplies it. Do not imply that the later
   visitor or recipient generated it unless the code proves that.
8. Do not write auth-dependent shorthand such as `<logged-in-user>` unless
   the path is actually guarded. Name the source of the value instead.
9. If a connected hop was not read, do not summarize through it. Mark that
   boundary as unresolved.
10. Keep tracing until the flow reaches a terminal effect, a final return,
    or an unresolved dynamic boundary.
11. When a branch depends on configuration (an env variable, config file,
    feature flag, or optional service such as a cache or queue), read what
    this workspace sets for it (`.env*`, config files, startup scripts) and
    record whether the branch runs here. A branch that the environment
    switches off is not a candidate cause. If the value cannot be found,
    mark the branch `Unknown in this environment`.

## Workflow

1. Restate the scenario in concrete terms.
2. Search for likely trigger points, then pick the real entry point by
   reading the surrounding code.
3. Read the trigger function top to bottom.
4. Record each step in execution order: the function, what calls it, the
   inputs it receives, the conditionals that change the path, and the call
   or side effect that follows.
5. For every downstream call, find the exact declaration and read it before
   continuing. Recurse function by function until the flow ends.
6. When the flow crosses a route, guard, resolver, redirect, service, or API
   boundary, read each connected hop before claiming end-to-end behavior.
7. Where branches exist, separate the confirmed path from alternate paths.
8. Check that no variable, declaration, handoff, or call site was skipped.
9. Save the report (next section) and reply with its path plus a short
   summary of the flow.

## Saving the report

Write the report under the directory this session was started in (its
initial working directory, even when a nested folder has its own `.git`):

    .nocturne/reports/<YYYYMMDD-HHMMSS>-<flow-slug>.md

Create the folder if it is missing. The slug is the flow name in lowercase
with hyphens, for example `20260131-142210-login-flow.md`. Nocturne keeps
`.nocturne/` out of git through `.git/info/exclude`; do not add it to
`.gitignore`.

### One report per flow

Asking for the same flow again must not leave two files. Before writing:

1. List `.nocturne/reports/` and read the frontmatter and "Trigger Point"
   of every report whose `session` is this session's id (see below for the
   id). In Claude, list and read with Glob and Read, not Bash (a Bash
   command stops to ask the user for permission). In Codex, use a plain
   shell command. Do not look for other tools or MCP resources.
2. One of them covers this flow when it starts from the same trigger point,
   or traces the same feature under a different wording, a narrower scope,
   or a wider one. A different title or slug does not make it a different
   flow.
3. If one does, write the new trace into that file. Keep its file name,
   `id`, and `session`; set `title` to the best name for the flow; set
   `updated` to the current time (format under "Updating a report"); and
   add a line under `## Revisions` saying what this run added or corrected.
   Do not create a second file.
4. Only a flow none of this session's reports covers gets a new file. A
   report written by another session is left alone, even for the same flow.

The file must open with this block, before the title. Nocturne reads it to
list the report against the session that wrote it:

    ---
    id: <the file name without .md>
    session: <session id>
    title: <Flow Name>
    ---

For `session`: when the request carries `Session: <id>`, use that id.
Otherwise use `${CLAUDE_SESSION_ID}`. If neither gives a real id (the value
still reads as a placeholder), write `unknown`.

## Updating a report

Nocturne asks for this by itself when a turn is about to end and a file a
report cites was changed after the report was written, so the report keeps
describing the code as it stands:

    update <doc-id> [<doc-id> ...]; changed: <file>, <file>, ...

Each `<doc-id>` is a report's file name without `.md`, in
`.nocturne/reports/`. The files after `changed:` are the cited files that
were written after the report was. Do not ask questions; for each report:

1. Read the report, then read each changed file it cites as it is now.
2. Re-check every claim that cites a changed file, and every later step
   that depends on one. The mandatory rules apply exactly as they do to a
   new trace: follow new calls, drop steps that are gone, and correct every
   `file:line` that moved.
3. Rewrite the report in place, under the same file name. Keep `id`,
   `session`, and `title` as they are, and keep every section the change
   did not touch.
4. Set the frontmatter's `updated` line to the current time, even when no
   claim had to change. Saving the file is what tells Nocturne the report
   was checked against the new code:

       ---
       id: <unchanged>
       session: <unchanged>
       title: <unchanged>
       updated: <YYYY-MM-DD HH:MM:SS>
       ---

5. Add one line at the end of the report, under a `## Revisions` heading
   (create it if missing): the time, the files that changed, and what the
   report now says differently, or "no claim affected".

If a named report does not exist, skip it and say so. Reply with one line
per report: its path and what changed. This is still investigation only: do
not change source code.

## Report template

```markdown
# <Flow Name>

## Request
<What the user asked to document>

## Trigger Point
- Confirmed trigger: <file:line> - <why this is the entry point>

## Confirmed Flow
1. <Function or trigger> - <what it does next> (`file:line`)
2. <Next function> - <inputs, branch, mutation, return, side effect> (`file:line`)

## Connected Flow Coverage
- Checked hop: <route/guard/resolver/helper/consumer that was read> (`file:line`)
- Unresolved boundary, if any: <hop not read and why> (`file:line`)

## Variables And Declarations That Matter
- <name> - <role in the flow> (`file:line`)

## Actor And Identity Context
- <who generates the value or link> (`file:line`)
- <which variable provides the dynamic segment> (`file:line`)
- <who later opens or receives it, and whether that path is guarded> (`file:line`)

## Environment
- <setting> = <value here, or "not set"> -> <branch it enables or disables> (`file:line`)

## Branches And Alternate Paths
- Confirmed branch on the documented path: <condition and outcome> (`file:line`)
- Alternate branch not taken or not confirmed: <condition and effect> (`file:line`)

## Terminal Effect
- <final return, DB write, API response, redirect, event, or render> (`file:line`)

## Evidence Ledger
- <claim> -> <file:line>

## Inferred Or Unresolved
- None
```

Drop "Actor And Identity Context" when the flow generates no link, token, or
identifier, and "Environment" when no branch depends on configuration.
Label every statement that is not backed by code you read as `Inferred`,
and end the flow on the last confirmed call or terminal effect.

## Answering from a report

The report exists so that answers rest on evidence, not on a plausible
story. When the user asks why something happens, why a value is wrong, or
what to change, in this turn or a later one:

1. Answer from the report's confirmed flow and its Environment section.
   If the question reaches a hop the report does not cover, trace that hop
   (update the report) before answering.
2. Name a cause only when the trace shows the path that produces the
   observed result in this environment. Otherwise say the cause is not yet
   confirmed, list what is confirmed, and say what would confirm the rest
   (a query, a log line, a request to observe).
3. Never tell the user to change data, settings, or code on the strength of
   an `Inferred` or `Unknown` claim.
4. When the user corrects a fact (for example "there is no Redis locally"),
   record it in the report's Environment section, drop every conclusion
   that rested on the wrong fact, and re-answer from what remains.
