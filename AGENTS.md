# AGENTS.md

Guidance for coding agents (Claude Code, Codex, others) working on Nocturne Terminal. Read this before changing any UI.

## What this is

A single-file GTK 3 + VTE terminal workspace written in Python (PyGObject). Everything lives in `nocturne.py`. There is no web stack, no framework, and no build step beyond `build.py`. Do not introduce new dependencies, toolkits, or files for UI. Extend the existing code instead.

- `CSS` at the top of `nocturne.py` is the only stylesheet. It is loaded at application priority for the whole screen.
- `PALETTE` holds the 16 terminal ANSI colors. UI accents come from the same palette.
- `AGENTS` defines session kinds (Claude, Codex, Shell). The + menu, the empty page buttons and the project right-click menu are all generated from it.
- The window has two views in `Window.views`: the **Terminal view** (default; `Window.terminal_view`, a `Workspace` with `plain=True`: shell tabs only, nothing saved, no Explorer or Git screens) and the **Agent view** (the sidebar plus project workspaces). `Window.set_mode('terminal' | 'agents')` switches between them, and the `view-switch` at the far left of the header is a segmented *Terminal | Agents* control with the current view highlighted (`.current`). The header title is one line set through `Window.set_header(title, subtitle)` (a 15px icon, `.header-name`, and the folder in a monospace `.header-path` pill); don't call `header.set_title()` / `set_subtitle()`, which the custom title hides. `current_workspace()` returns the Terminal view's workspace while it is shown. Agent view features must stay hidden and their shortcuts inert in the Terminal view. Never start agent sessions before the Agent view is opened.
- `Workspace` is one project: a `Gtk.Paned` holding the `Explorer` (optional, left) and a `Gtk.Notebook` whose pages are terminals (a page with `.term`) or `Editor`s (a page with `.term = None`). Use `terminals()` / `editors()` / `editor()` instead of walking notebook pages yourself. The notebook has no tabs of its own (`show_tabs=False`): add pages only with `Workspace.add_page(page, tab, tooltip, on_press)`, which builds the tab for the **tab strip**. `refresh_strip()` draws the tab strip: in the Agent view only the tabs of the group picked in the header (`strip_group`; `page_group()` decides which), in the plain Terminal view all its shells. A page's tab is `page.handle`; destroy it together with the page. `Window` holds the sidebar, dialogs, persistence and shortcuts.

## UI rules

New UI must look like it was always part of Nocturne. Never ship a stock GTK or Adwaita widget without styling it.

### Colors (use these, do not invent new ones)

| Role | Value |
| --- | --- |
| Terminal / page background | `#292b36` |
| Header bar, tab strip, entries | `#262832` |
| Status bar | `#232530` |
| Sidebar, popovers, menus (raised surface) | `#30323d` |
| Borders on raised surfaces | `#464857` (popover, menu), `#3e404c` (sidebar edge, separators) |
| Header and tab borders | `#343642` |
| Hover | `#3a3c48` (list rows), `#42444f` (buttons) |
| Selected / menu hover | `#4a4459` |
| Primary text | `#dedee8` |
| Bright text (titles, selected, hover) | `#f0eaf5` / `#ece8f4` |
| Muted text | `#9a9cb0`, `#8e90a2`, `#8e90a4` (section headers) |
| Hint / shortcut text | `#7c7e92`, monospace 11px |
| Disabled text | `#626476` |
| Accent (pink) | `#d18fb9`, hover `#df9fc8`, tab underline `#db8dbc`, cursor `#df9bc6`. Text on an accent background is `#1f1c27` |
| Error / missing | `#ef8195` |

Identity colors, one per kind. The same color is used for the tab dot (`●`) and the menu row dot:

| Class | Color | Used for |
| --- | --- | --- |
| `.agent-claude` | `#e7a37c` | Claude |
| `.agent-codex` | `#8fb7ec` | Codex |
| `.agent-shell` | `#a6d8a0` | Shell |
| `.git-flow` | `#e7ca8b` | Git Flow and other git tabs |
| `.git-kraken` | `#8dd4d0` | GitKraken |
| `.file-tab` | `#cd9cdb` | Explorer, folder icons, editor tabs |
| `.agent-tools` | `#ff9cab` | Skills |
| `.note-tab` | `#f4dba6` | Notes |

A new kind gets a new class whose color comes from `PALETTE` and isn't already used for another kind.

### Appearance (system Light / Dark and fonts)

- Colors and fonts follow the desktop, live. `read_appearance()` reads GNOME's `org.gnome.desktop.interface` (`color-scheme`, `font-name`, `monospace-font-name`; `interface_settings()` returns None elsewhere, and Nocturne stays dark) into `LIGHT`, `UI_FONT` and `MONO_FONT`; `NOCTURNE_APPEARANCE=light|dark` overrides the style (`check.py` pins dark). `App.apply_appearance()` restyles everything open when a key changes.
- The colors in this file are the **dark design**; always write them. `LIGHT_TONES` maps each one to its light counterpart and `tone(color)` returns the color for the current appearance. `themed_css()` applies it to `CSS` (hex and `rgba()`), `rgba()` and `hex_rgb()` apply it in code, and `icon_pixbuf()` recolors the SVG icons. Colors that bypass those (text-tag `foreground=` strings, Pango markup) must go through `tone()`. A new palette color needs a `LIGHT_TONES` entry. Terminals use `PALETTE_LIGHT` in Light (`style_terminal()`), editors the `nocturne-light` scheme (`editor_scheme()`; `assets/styles/nocturne-light.xml` is generated from `nocturne.xml` with `LIGHT_TONES`, so regenerate it when you change the dark one).
- Fonts: don't set `font-family` except `monospace`, which `themed_css()` swaps for the system monospace font. CSS `font-size` pixel values are drawn for an 11pt interface font and scaled by the system font size. Terminals and editors use `MONO_FONT` at its own size.

### Shape and type

- Radius: 6px for buttons, rows, entries and menu items; 9px for badges; 10px for popovers.
- Section headers are UPPERCASE, 10px, bold, letter-spacing 2px (`.section`, `.list-header`, `.menu-header`).
- Paths and shortcuts use monospace (`.muted` inside list rows, `.menu-hint`). Terminal and editor font is the system monospace font (`MONO_FONT`).
- Buttons are flat (transparent background, no border, no shadow). Use the `accent` class for the single primary action and the `outline` class for secondary actions.
- No text shadows, no icon shadows, no gradients.

### Header tools and screens

- Git features live at the **top right of the header bar**, as icon `Gtk.ToggleButton`s with the `header-tool` class: Skills, Git Flow, then GitKraken. They sit to the left of the close button. Icons are SVGs in `assets/icons/`, drawn as 16px line icons in the feature's identity color and loaded with `icon_image(name, size, widget)`. A new tool gets its own SVG in the same style, never a stock icon or a brand logo.
- A tool opens a **screen**: a `GitScreen` page in the workspace's stack (`Workspace.show_screen(name)`, `None` returns to the terminals). Screens are per project, keep the sessions running, close on Esc or *Back to terminals*, and keep the toggle in sync through `Window.sync_screen_buttons()`.
- Screen layout: a centered column with a header (34px icon, `.screen-title`, `.muted` subtitle, then *Refresh* and *Back to terminals* outline buttons on the right), followed by `.card`s built with `card(title, hint)`. Use `facts()` for key/value rows (`.key` / `.value`), `actions()` for button rows, and a `Gtk.ListBox` for selectable lists. Clamp long labels with `set_max_width_chars` so every screen keeps the same width.
- Semantic text classes: `.ref` (branches, refs; pink), `.add` / `.ok` (green), `.del` (red), `.warn` (yellow; dirty state, commit hashes), `.mono` (paths, messages), `.menu-hint` (small monospace metadata).
- Diffs render in a read-only `Gtk.TextView` inside `.diff`, colored with tags: add `#a6d8a0`, del `#ef8195`, hunk `#8dd4d0`, meta `#8e90a2`. Change counts are always written `+N −N`, with the true minus sign (`−`).

### Explorer and editor

- The Explorer is the first group switch at the **top left**, because the panel opens on the left: it shows the open files and opens the tree; clicked again it hides or shows the tree. `Workspace.toggle_explorer()` shows or hides the tree; `Window.sync_group_buttons()` keeps the button in sync.
- Explorer surface `#262832` with a `#343642` right border; rows use the same selected color (`#4a4459`) as the sidebar. Git status colors file names: yellow M, green A/U, red D/!, cyan R. Folder and file icons are SVGs in `assets/icons/`.
- Editors use GtkSourceView 4 with the `nocturne` style scheme in `assets/styles/nocturne.xml`. Change editor colors there, using palette colors only. Problem underlines are the `error` (`#ef8195`) and `warning` (`#e7ca8b`) text tags.
- Editor chrome, top to bottom: `.editor-notice` (changed on disk), `.editor-find`, the text, the `.problems` list, then `.editor-status` (11px muted labels; chips such as *Format on save* use the `chip` class and turn pink when on).
- Language tools are found with `find_tool()` (project `node_modules/.bin`, `.venv/bin`, then the interactive shell's PATH from `shell_path()`) and always run through `run_tool()`, which is asynchronous. Never block the UI on a linter or formatter. Add a linter in `linter_for()` and a formatter in `formatters_for()`. Built-in fallbacks are `builtin_problems()` and `builtin_format()`.
- Editors never lose text: formatting is skipped if the buffer changed while the tool ran, reloads only happen to clean buffers, and every way of closing a dirty editor goes through `ask_save()` / `settle_unsaved()`.

### Tab strip

- Agent view header, left, icons only and in this order: ☰ (`sidebar_toggle`, `menu.svg`; shows or hides the projects list), ⌂ (`home_button`, `home.svg`; Terminal view), then one `header-tool` toggle per tab group (`Window.group_buttons`: Explorer = `files` first, then Agents, Terminals, Notes; `explorer_button` is the Explorer one) with a `.tool-count` beside the icon (pink `.attention` when a turn ended in that group). `Window.sync_group_buttons()` keeps the checked one on `Workspace.strip_group`; the segmented *Terminal | Agents* switch is only shown in the Terminal view.
- The Agent view's tab row is the same browser-style strip as the Terminal view (`.term-strip`), showing **only** the tabs of `strip_group`: groups are isolated, so files never appear among agents or shells. The + (the session menu) sits right after the last tab and is hidden in the Explorer group. Choosing Explorer also opens the tree; clicking it again while it is selected hides or shows the tree (as Ctrl+Shift+E does), and opening a file switches to the Explorer group. There are no ▾ menus and no group-tab row. The left side of the paned is one `Workspace.side` stack shared by the group panels: the Explorer tree (`'explorer'`, Explorer group, `explorer_open`) and the `SessionsPanel` (`'sessions'`, Agents group, `sessions_open`; clicking Agents again toggles it); Terminals have none. While the sessions panel is open, the Agents strip shows only the current tab (the panel does the switching); with it hidden, all agent tabs. `update_side()` picks it, and `refresh_strip()` calls it. Never pack anything else into `paned.pack1`. The sessions panel is styled like the Explorer (`.explorer .sessions-panel`): a `.session-search` entry, `.session-section` headers *OPEN* and *HISTORY*, and `.session-row`s. Both lists are latest active first: *OPEN* sorts by `term.active_at` (set when the tab is started, shown, or its turn ends; a restored conversation uses its transcript's mtime from the history), *HISTORY* by transcript mtime. History comes from `agent_history()` (Claude transcripts under `CLAUDE_CONFIG_DIR/projects/<folder>`, Codex rollouts whose `session_meta.cwd` is the folder, named like the CLIs name them), read in the background and cached by file mtime in `HISTORY_CACHE`; it only reads the CLIs' files, never writes them. Tabs rename in place on double-click; an agent tab's dot turns pink (`.attention`) when its turn ended while you were elsewhere. `show_group()` still switches group (back to `last_page`, or the group's empty page in `self.content`).
- **Notes** (`'notes'` group): `Note` pages (`page.term = None`; use `note_pages()` / `note()`) are Markdown files (`# Title`, a blank line, the body; `split_note()` / `join_note()`) in `notes_dir(project)`, beside `STORE` and never in the project. They save themselves 500 ms after a change (temp file, then swapped in), on close and in `confirm_window_close()` (`save_notes()`); a note closed empty deletes its file, and a clean note reloads when its file changes. The side panel is `NotesPanel` (`'notes'` in `Workspace.side`, `notes_open`; styled like the sessions panel, *OPEN* then *NOTES*, search matches contents through `list_notes()`, cached by mtime in `NOTE_CACHE`). The + is hidden in the Notes group; new notes come from the panel's buttons, the empty page, or `paste_note()` (clipboard read with `request_text()`, never blocking). Open notes are saved in `view_state()['notes']`.
- A new group goes in `TAB_GROUPS` with an icon in `GROUP_ICONS` and its empty-page actions in `show_group()`; it gets a header toggle automatically.
- The plain Terminal view has browser-style tabs (`.term-strip`): `.strip-tab`s with 6px top corners, muted `.tab-title`, `#2e303b` hover, and the active tab in the page background with a pink `inset 0 2px #db8dbc` top line, so it joins the **context bar** below (`.term-context`, page background, `#343642` bottom border). Tabs are named by `shell_title()`; a `.tab-meta` pill shows the foreground command (`.running`, cyan) or the git branch, and the dot turns pink (`.activity`) on output in a background tab (`mark_activity()`). The × shows on hover and on the active tab only. The + sits right after the last tab, not at the far right.
- The context bar (`context_bar()`): folder breadcrumb (copies the path), branch chip, running chip, `cols × rows`, and *Find* / *New here* / *Open folder*. `poll_context()` refreshes it and the tab pills every second from `/proc` (`term_context()`, `git_head()` reads `.git` directly); never run git or other commands there. The header subtitle follows the current tab's folder.
- Find in scrollback (`find_bar()`, `.editor-find .term-find`) sits above the pages in both views and uses VTE's search with a literal (escaped) pattern.
- The Explorer draws its own right border; `paned > separator` stays transparent (a colored separator shows as a 10px bar).

### Menus

- **The + menu** is only for new sessions. It is a `Gtk.Popover` with the `session-menu` class (`Workspace.session_menu`). Add entries with `Workspace.menu_row(text, callback, dot_class, hint)`. Each row has a colored dot, a label, and a right-aligned hint (usually the shortcut). Group rows under a `.menu-header` label and split groups with `Gtk.Separator()`. Do not put feature screens (Git, etc.) in it; they belong in the header.
- **Right-click menus** (`Gtk.Menu`) are themed through the `menu` / `menuitem` CSS. They are fine for short context menus. Use `Gtk.SeparatorMenuItem` between groups.
- Do not go back to `Gtk.MenuButton(popup=Gtk.Menu)` for the + menu. That was the unthemed version.

### Dialogs and feedback

- Use the `Window` helpers: `message(text)`, `confirm(text)` and `ask_text(title, text, hint, action)`. Do not create ad-hoc dialogs.
- Long-running or visible commands (git operations, installs) run in a new tab: `workspace.new_session('shell', record={...name...}, command=..., requires='<binary>', style='<dot class>', transient=True)`. Transient tabs are not saved or restored.
- Pass user input to shell scripts as positional arguments (`shlex.join(['bash', '-c', SCRIPT, 'name', arg1, ...])`). Never interpolate it into script text.

### Shortcuts

- App shortcuts use Ctrl+Shift+letter and are handled in `Window.on_key`. Plain Ctrl+C and Ctrl+V are reserved for the terminal. Inside an editor, plain Ctrl+S saves and Ctrl+F finds.
- In use: A (Terminal view / Agent view), O, T, L, X, R (rename session or note), E (Explorer), J (Notes), I (format document), G (Git Flow screen), K (GitKraken screen), H (Skills screen), W (close tab), F (find in scrollback), C, V, PageUp, PageDown (projects in the Agent view, tabs in the Terminal view). Plain Esc closes a screen. Check this list before adding one.
- Show the shortcut as the hint in its menu row or in its header button's tooltip, and add it to the status bar text in `update_status`.

## Agent sessions

- Renaming a Claude or Codex tab goes through `Workspace.set_session_name()`, which also renames the CLI's own conversation with `rename_agent_session()`: a `{"type": "custom-title"}` line appended to the Claude transcript, or a `session_index.jsonl` entry for Codex. Both CLIs keep the last record. Never type commands into a running agent's terminal to do this, because it could submit text the user has half typed.
- In the Terminal view, tabs are renamed in place (`rename_session()` swaps the title for a `tab-rename` entry); in both views the tab's name becomes an entry in place; a tab that isn't on screen asks with `ask_text()`. The tab widgets in the strip must not be rebuilt on refresh, or double-click and inline editing break; `refresh_strip()` only moves them when the visible set changes.

## Skills and hooks

- The **Skills** screen (`AgentToolsScreen`) shows only the skills that ship in `assets/cli/.claude/skills`, across the whole page: a `.skill-side` list on the left, and on the right a `.skill-detail` panel with the description, how to run it, examples, the SKILL.md instructions and an On/Off `chip`. Each example appears once; a Claude/Codex pair of chips switches the `/` or `$` prefix, so never list the same example twice. Do not add other skills, hooks, rules, memory, reports or activity back to this screen.

- **Nothing loads at session start.** Every skill has `disable-model-invocation: true` in its SKILL.md front matter and `policy: allow_implicit_invocation: false` in `agents/openai.yaml`, so neither CLI loads or picks it on its own. The session-start hook adds nothing when a session starts, resumes or clears; only after a compact (`source: compact`) does it load the session's own flow reports again (`compact_context()`). The other automatic steps are the **impact check before and after every edit** (PreToolUse / PostToolUse, `analyze_edit()`; it must stay, it is what prevents regressions; edits come from Claude's Edit/Write, Codex's `apply_patch`, and shell commands, which `take_snapshot()` / `shell_edits()` catch by comparing the code files before and after any command that isn't `read_only()`) and report upkeep at a turn's end (Stop). Do not add skill lists, memory or other context to session start.
- **Project memory on every prompt, through an index.** Like a book's table of contents and index, so memory can grow without bloating the context. The prompt hook first refreshes the memory index (`memory_index()`: `.nocturne/index/index.json`, rebuilt per file when one changes, covering `.nocturne/memory/` and `.nocturne/reports/`; `index_document()` records each file's title, summary (the `description`), `keywords`, sections with line numbers and the words in each), writes the readable `.nocturne/index/CONTENTS.md` (`contents_page()`: a detailed table of contents and an alphabetical index), then looks the prompt up in it (`prompt_terms()` / `text_words()`: identifiers split, `TERM_ALIASES` such as db = database, while words every coding request uses (repo, app, file, project, issue, image) are in `MEMORY_STOPWORDS` and never decide relevance; bump `INDEX_VERSION` when the word rules change; `score_documents()`: weighted by field and rarity, weak matches dropped). Only then does it load anything: the matching entries (whole, or just their matching sections via `section_text()`) and pointers to matching flow reports. Unmatched entries never enter the context, not even as a list; with no match the context is one line pointing to CONTENTS.md. The instructions say to answer from memory before searching the code or the web, and to cite the file. Never hand-edit `.nocturne/index/`. Each injection is also shown to the user as the hook's `systemMessage` (`memory_notice()`: *Nocturne checked the memory index: N entries, M matched (names); loaded X of Y chars*; nothing when the project has no memory), which both CLIs print in the conversation, and logged as `project-memory` in `activity.jsonl`. Codex records it as a developer message in its rollout, which is how to confirm it arrived.
- **Memory upkeep, never visible** (`memory-upkeep`, Claude and Codex only). When a prompt corrects the agent or states a rule (`cue_sentences()` / `MEMORY_CUES`: "actually", "always", "we use", "remember"...), `memory_upkeep_note()` adds one short paragraph to the prompt's `additionalContext`, which neither CLI prints: the user's sentences quoted back, the entries that already cover the subject (matched at prompt time plus `score_documents()`), the entry format in one line, pairs to merge from `overlapping_entries()` (Jaccard ≥ `OVERLAP` or containment ≥ `CONTAINED`; once per change, in `upkeep.json`), and the `Memory: …` reply line. Other prompts get nothing. **Never use Stop's `decision: block` for memory or put long text in a reason**: both CLIs print the whole reason in the conversation ("Blocked by hook"), it stays in the agent's context, and continuing every task broke the user's flow (a 5 KB version caused compactions that lost the task). The prompt hook records when the turn began in `cli/memory-turns/<session>.json`; after any tool that writes `.nocturne/memory/*.md` from then on (Edit/Write, `apply_patch` or a shell command), `memory_lint_notes()` → `lint_memory_entry()` checks it against the standard in `nocturne-remember/SKILL.md` (frontmatter, name = file name, a 20–160 char description, a known type, 3+ keywords, ≤ 15 body lines, no secrets, no relative dates, progress notes, hedging or `Update:` lines) and names each problem once per version (`linted`), in the post-tool context, also unprinted. Keep `lint_memory_entry()` in step with the skill's standard. It never writes memory itself and never logs the prompt.
- Ported from Atelier's CLI integration. Everything ships in `assets/cli/`: the skills in `assets/cli/.claude/skills/nocturne-*/SKILL.md` (plus `agents/openai.yaml` for Codex's menu), and one hook script, `assets/cli/hooks/nocturne_hook.py` (standard library only, run with Nocturne's own `sys.executable`). It handles `session-start`, `prompt`, `pre-tool`, `post-tool`, `stop` and `--diff`, and holds `BUILTIN_HOOKS`, `RULE_EVENTS` and `RULE_ACTIONS`. `nocturne.py` imports it with `hooklib()`, so the UI and the hooks share one list. Add a built-in hook there, not in `nocturne.py`.
- The impact check parses with **tree-sitter**: `assets/cli/treesitter/` holds web-tree-sitter (`tree-sitter.cjs`, `tree-sitter.wasm`, MIT) and grammar WASMs (Unlicense), copied unchanged from npm, plus `impact.cjs`, which returns symbols and identifier uses (import / call / reference) as JSON. The hook calls it once per edit through `ts_run()` with Node (`NOCTURNE_NODE` from `agent_env()`, else `node` on PATH). If Node is missing or fails, `TS_FAILED` switches the run to the built-in parsers (`py_symbols`, `js_symbols`), so the check never breaks. Uses are only matched within a language family (`family()`). A new language needs its grammar WASM, an entry in `TS_LANGS`, and its definition and identifier node types in `impact.cjs`.
- `new_session()` launches agents through `agent_launch()`. Claude gets `--add-dir <cli>/skills --settings <cli>/claude-settings.json`; Codex gets `-c features.hooks=true -c features.memories=false -c hooks.<Event>=[...]`. Codex's hook commands name only the `NOCTURNE_HOOK_PYTHON` / `NOCTURNE_HOOK` environment variables from `agent_env()`. Keep them path-free and stable: Codex trusts a hook by a hash of its command, and changing the command makes users review it again in `/hooks`.
- Other apps' skills that do a Nocturne skill's job (Atelier links `atelier-impact-check` and others into `~/.codex/skills`) would be picked by Codex on its own. `shadowing_skills()` finds them by name suffix and `agent_launch()` switches them off for Nocturne's Codex sessions only, with `-c skills.config=[{name=…,enabled=false}]` (verified with `codex debug prompt-input`). Never delete or edit another app's skills.
- Never write to `~/.claude` or `~/.codex` settings, and never write Codex's `hooks.state` trust yourself. `codex_hooks_trusted()` only reads it, so a new Codex tab can remind the user to approve the hooks in `/hooks`. The only thing written to a CLI home is `link_codex_skills()`: symlinks named after the app skills, and never over a real folder.
- State lives beside `STORE`: `agent-tools.json` (switches, rules, protected branches; read by the hooks on every call) and `cli/` (`claude-settings.json`, synced `skills/`, `activity.jsonl`, `approvals/`, `turns/`, `snapshots/` (code-file stats taken before a shell command, removed after it), `upkeep.json`). Tests stay isolated by setting `n.STORE` and `CODEX_HOME`.
- The package guard (`package_intent()`) asks only for commands that change dependencies, publish or run registry packages (`PACKAGE_CHANGES`: install, add, remove, update, ci, publish, dlx...); project scripts (`npm test`, `npm run typecheck`, builds, `npx tsc`) never need approval. A refusal is remembered per session and command for `REFUSAL_MEMORY` (2 minutes) in `cli/approvals/refused.json`, only to stop a retry loop; never make it permanent (a stored "no" once blocked a typecheck for hours without asking).
- Codex hooks cannot `ask`, so an approval guard writes `cli/approvals/<id>.json` and waits for `<id>.answer`. `Window.poll_agent_tools()` touches `cli/alive`, answers with `confirm()`, and reads turn-end markers (`cli/turns/<NOCTURNE_TERM_ID>`), which set `term.turn_done`. Hooks continue a turn only through Stop's `decision: block`, never by typing into the terminal.

## Git safety

- Never commit, merge, push, tag, or delete branches without an explicit user action. Init and Finish list their steps in `confirm()` first.
- Never overwrite existing `gitflow.*` or other git config. Only set values that are unset.
- Never discard changes. Refuse to run an operation on a dirty tree when it would put that work at risk.
- Stop on the first failure, so conflicts are left for the user to resolve in the tab.

## Logs

- **When the user reports a problem (a freeze, a crash, something not working), read `~/.local/state/nocturne/nocturne.log` first**, around the time they mention, before guessing. Older entries roll over to `nocturne.log.1` at 2 MB.
- `start_logging()` (only when Nocturne runs, never in `check.py`; `LOG_PATH` stays `None` there) records: `[start]` with versions and the session type, `[error]` uncaught exceptions and failed background tasks, `[warning]` / `[critical]` GTK and GLib messages, `[run]` every command `stream_run()` shows (with exit code, time, and the output tail on failure), `[slow]` probes from `wait_output()` that took over 5s or blocked the UI thread over 0.3s, and `[freeze]` when the main loop stops for over 2s: the Python stack it is stuck in, every thread after 10s, and how long it lasted. Hard crashes are written there by `faulthandler`.
- Log through `log(kind, text)`. Don't log prompts, tokens, or file contents.

## Validation

Before you finish a change, run:

```sh
python3 -m py_compile nocturne.py
python3 check.py          # GUI integration test; opens a window briefly and rewrites preview.png
```

To check UI work visually, take a screenshot. On Wayland, popovers and menus are separate surfaces, so run the screenshot script with `GDK_BACKEND=x11` to capture them inside the window (`Gdk.pixbuf_get_from_window`). Compare the result against the existing UI before calling it done.

**Always build and install when a change is done.** After the checks pass, run `python3 build.py --install`, then confirm the installed copy matches the source (`cmp nocturne.py ~/.local/share/nocturne/nocturne.py` and `diff -rq assets ~/.local/share/nocturne/assets`). A change is not finished until it is installed. Tell the user that a running Nocturne keeps the old code until it is restarted.

## Docs

User-facing behavior lives in `README.md` (the **Use** section). Update it whenever you add a menu item, shortcut, or session kind.
