---
name: nocturne-remember
description: Save a lasting fact about this project to the workspace memory in .nocturne/memory, where /nocturne-re-init-feature reads it back. Use when the user says remember, note that, keep in mind, or corrects a fact about the environment, a name, or how they want work done. Also removes an entry when the request starts with forget.
disable-model-invocation: true
---

# Nocturne Remember

Everything after the command is what to remember, in the user's own words.
Turn it into one entry of this workspace's memory. If nothing was given,
ask what to remember and stop.

The memory is in `.nocturne/memory/` under the directory this session was
started in. This skill's own files ship inside the Nocturne app and are not
part of the project: never search or read the folder they live in. The project-memory prompt hook recalls these facts before each task prompt;
`/nocturne-re-init-feature` can also read them back when asked. Nocturne's
prompt hook also asks the session to save it when the request corrects the agent or states a rule, so use
this skill for what the user wants kept on purpose.

This only writes memory. Do not change source code, and do not trace or
investigate the fact. The memory is only the files in `.nocturne/memory/`.
Use exactly these tools and nothing else; do not look for other tools,
MCP resources, or connectors:

- Claude: Glob or Read to list and read, Write or Edit to save, Bash `rm`
  to forget.
- Codex: a plain shell command to list and read (`ls`, `cat`), and
  `apply_patch` to save or delete. In `apply_patch`, every line of an added
  file starts with `+`, the frontmatter `---` lines included:

      *** Begin Patch
      *** Add File: .nocturne/memory/no-redis-locally.md
      +---
      +name: no-redis-locally
      +description: There is no Redis in the local environment
      +type: environment
      +keywords: redis, cache, local
      +---
      +
      +Local runs have no Redis.
      *** End Patch

  Change an existing entry with `*** Update File:`, remove one with
  `*** Delete File:`.

## Saving a fact

Apply the memory standard below. Nocturne checks every entry against it
right after it is written and names anything to fix.

<!-- memory-standard:start -->
### Memory standard

Memory is what a fresh agent, starting with no conversation, needs in order
not to repeat a mistake or ask again. Nocturne loads an entry only when a
request shares words with its name, description or keywords, so an entry that
is badly named is as good as lost.

**1. Review the task for each kind of lasting fact, one by one.** Go through
the conversation and ask, for each kind, "did this task establish one?":

- `correction`: the user said an agent (you or another) got something wrong.
  Record the right fact and name the wrong one ("not Docker").
- `environment`: what runs, exists or is configured in which environment
  (local, dev, staging, prod): services, ports, hosts, env variable names,
  versions, credentials *by location only*.
- `naming`: what a word, codename or abbreviation means here (which repo,
  app, service, table or team).
- `preference`: how the user wants work done: style, review habits, what to
  ask before doing, tools to use or avoid.
- `fact`: any other lasting project fact: a decision the user confirmed, a
  gotcha that cost time (symptom, cause, fix), the command that builds, tests
  or deploys, a limit or quota.

**2. Keep only facts that pass all three tests.**

- *Lasting*: still true and useful a month from now.
- *Not obvious*: a fresh agent would get it wrong or have to ask; it is not
  plainly written in the code, README or config.
- *Confirmed*: the user said it, or this task verified it (command output, a
  file you read). Never save a guess, an assumption or an unverified cause.

Never save: secrets (passwords, tokens, keys, connection strings with
credentials; name where they live instead); task progress, plans or TODOs;
how some code works (that is a flow report); instructions meant only for
this task; anything about people beyond their role.

**3. Never duplicate.** One subject per entry, one answer per question.

- List `.nocturne/memory/` and read each entry's frontmatter first.
- If an entry covers the same subject (same service, setting, name or
  habit, however worded), change that entry: edit only the lines the new
  fact changes and keep every other line. Never shrink an entry to the new
  fact alone.
- A line anywhere in memory that the new fact contradicts is corrected in
  place, not appended to ("Update: ..." lines are not allowed). Keep the old
  value only when it helps ("port 5544, moved from 5433").
- Two entries about one subject are merged into one that keeps every
  distinct fact; the other file is deleted.
- Create a new file only when no entry covers the subject.

**4. Write the entry in this format.** File `.nocturne/memory/<name>.md`:

    ---
    name: <name>
    description: <the fact itself, one sentence, 20 to 160 characters>
    type: <correction | environment | naming | preference | fact>
    keywords: <3 to 8 comma-separated words>
    ---

    <the fact in one plain sentence: the rule first>
    <scope: where or when it applies, if that matters>
    Why: <the reason, only if the user gave one>
    Check: <a file path or command that confirms it, if there is one>

- `name`: 2 to 5 lowercase words joined by hyphens, naming the subject
  (`local-postgres`, `tiger-codename`, `deploy-command`), equal to the file
  name without `.md`.
- `description`: a complete statement of the fact, not a title. "Local
  PostgreSQL runs natively on port 5544, not in Docker", not "Database
  info". It is shown in the table of contents and matched against requests.
- `keywords`: the words someone would use when asking about it, including
  other names for the same thing and exact identifiers: `postgres, database,
  db, port, 5544`. Lowercase.
- Body: at most 15 lines. Present tense, plain statements, no hedging
  ("maybe", "I think"). Exact identifiers, paths and commands in backticks.
  Absolute dates ("2026-10-19"), never "today" or "last week". Say which
  environment it applies to when that matters. Keep the user's meaning
  exactly; add nothing the user did not say or the task did not verify.

Good:

    ---
    name: local-postgres
    description: Local PostgreSQL runs natively on port 5544, never in Docker
    type: environment
    keywords: postgres, postgresql, database, db, port, docker
    ---

    Locally, PostgreSQL runs natively under systemd on port `5544`, not in Docker.
    Login user is `app_rw`; its password is in `backend/.env` (`DB_PASSWORD`).
    Check: `systemctl status postgresql`

Bad: `name: notes`, `description: Some DB stuff`, no keywords, "Today I
found the db is probably on 5544, password hunter2".

**5. Finish with one line.** At a turn's end, about the whole task, counting
memory changed earlier in it: `Memory: updated <file>`, `Memory: added
<file>`, `Memory: merged <file>` or `Memory: no change`. (Run as
`/nocturne-remember`, use the Reply format of that skill instead.)
<!-- memory-standard:end -->

## Forgetting

When the request starts with `forget`, find the entry that covers what
follows and delete its file. If no entry matches, say so; if more than one
might, name them and ask which.

## Reply

One line, nothing else:

    Remembered: <kebab-name> (<type>): <description>

or `Updated: ...`, or `Forgot: <kebab-name>`. Do not repeat the file's
contents or explain the memory system.
