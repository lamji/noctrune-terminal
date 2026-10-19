---
name: nocturne-debugging
description: Diagnose and fix a reproducible defect with evidence, a narrow plan, and focused verification. Use for a reported bug, broken UI behavior, runtime error, regression, or unexpected output.
disable-model-invocation: true
---

# Nocturne Debugging

Use this skill for a reported bug, broken UI behavior, runtime error,
regression, or unexpected output. Everything after the command describes
the defect. If nothing was given, ask what is wrong before reading anything.

## Investigate before editing

1. Treat screenshots, logs, and prior summaries as leads, not proof of the
   current implementation.
2. Locate the visible trigger or concrete entry point, read its owner, and
   trace the handler, data, and rendered result through the divergence. When
   a flow report in `.nocturne/reports/` covers the path, start from it and
   re-read what it cites.
3. Check which branches this environment takes (env files, config, feature
   flags) before blaming a service or fallback that may not run here.
4. Read every file you will change before editing. Keep the fix limited to
   the confirmed path.
5. For a change, write a short file-specific plan before the first edit and
   complete its steps in order.

## Fix and verify

1. State the observed behavior, expected behavior, and the smallest
   confirmed cause, with `file:line`.
2. Change the live owner rather than adding a workaround elsewhere.
3. Read the impact check Nocturne adds before and after each edit, and keep
   every listed site working.
4. Re-read the connected caller and render path after the edit.
5. Run the narrowest relevant check, test, or preview. Report the evidence
   and any remaining limitation.

## Rules

- The same workflow applies to Claude and Codex. Their tool mechanics
  differ; the evidence, plan, edit, and verification requirements do not.
- Do not ask whether to proceed once the defect and safe default are clear.
- If the user corrects a fact about the environment, save it with the
  `nocturne-remember` skill so later sessions know it.
