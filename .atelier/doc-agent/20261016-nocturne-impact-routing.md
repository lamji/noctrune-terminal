---
id: 20261016-nocturne-impact-routing
session: unknown
title: Nocturne impact routing
---
# Nocturne impact routing

## Trigger Point
Nocturne launches an agent through `Workspace.new_session` (`nocturne.py:2261`), which calls `agent_launch` (`nocturne.py:674`).

## Confirmed Flow
- `prepare_agent_tools` links Nocturne skills into the existing Codex home without removing other skills (`nocturne.py:658`, `nocturne.py:609`).
- `agent_launch` attaches UserPromptSubmit and edit hooks (`nocturne.py:674`).
- `on_session_start` returns no context for startup/resume/clear (`assets/cli/hooks/nocturne_hook.py:1043`).
- `on_prompt` previously provided only user rule notes (`assets/cli/hooks/nocturne_hook.py:1061`).
- The edit hook produces Nocturne impact context via `format_impact` (`assets/cli/hooks/nocturne_hook.py:937`).

## Environment and boundaries
The current skill catalog contains atelier-impact-check. Nocturne does not remove global skills. The screenshot shows the agent selecting Atelier; the exact model selection decision is not observable. Missing routing guidance is confirmed; deterministic model compliance cannot be guaranteed.

## Impact
Change on_prompt context only. Its consumer is hook main dispatch; check.py exercises JSON context, rules and startup behavior. Preserve blocking rule priority and the impact hook toggle.
