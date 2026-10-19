# CLAUDE.md

The project guidance for all coding agents lives in AGENTS.md, and it applies here in full:

@AGENTS.md

## Claude Code specifics

- This session may be running inside Nocturne itself. Don't kill Nocturne processes or edit `~/.config/nocturne/workspace.json`. Test against temporary workspaces the way `check.py` does (`n.STORE = <temp>/workspace.json`).
- Put screenshots and scratch repositories in the session scratchpad, not in the project folder.
- Always finish a change by running `python3 -m py_compile nocturne.py`, `python3 check.py`, then `python3 build.py --install`, and verify the installed copy matches the source. Don't stop before it is installed, and remind the user to restart Nocturne.
