---
id: 20261016-group-dropdown
session: unknown
title: Workspace group dropdown lifecycle
---
# Workspace group dropdown lifecycle

## Trigger Point
The group caret is a Gtk.MenuButton created by Workspace.refresh_strip (`nocturne.py:2172`). Its toggled callback populates an initially empty popover (`nocturne.py:2177`, `nocturne.py:2041`).

## Confirmed Flow
- Session title notifications queue refresh_strip (`nocturne.py:1980`); VTE window-title-changed updates that title (`nocturne.py:2288-2296`).
- queue_strip schedules an idle refresh (`nocturne.py:2009`).
- refresh_strip destroys every group and its caret/popover (`nocturne.py:2139`) and rebuilds all three (`nocturne.py:2145-2180`).
- fill_group_menu reads only this workspace's pages and builds switch/rename/close rows (`nocturne.py:2041-2110`).
- Window.turn_ended also queues refresh for the matching workspace (`nocturne.py:7436-7447`).

## User observation and unresolved boundary
User reports nothing appears for all three dropdowns in one workspace; another works. Destruction of popovers on refresh and initially empty popovers are confirmed lifecycle defects. Which event occurred during the user's click cannot be confirmed without a live reproduction. GUI access is unavailable in this sandbox.

## Impact
Keep group/caret identities stable across refreshes, retain current callbacks and plain terminal strip behavior, populate before popup. Regression coverage in check.py should assert identity across title/page updates and build every group in both workspaces.
