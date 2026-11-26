"""Packs: three small add-ons for Claude Code, each one switch, off until turned on.

  memory      "It forgets between sessions": search over every past chat on this Mac (no model
              calls), a health check of Claude Code's own memory index, and claude-mem managed so
              it watches your chats only, with a weekly cap.
  codemap     "It doesn't know my codebase": finds a code map you already have (graphify,
              codegraph), gives the exact commands to add codegraph, and adds one removable
              instruction that tells Claude Code to ask the map before reading files.
  houserules  "Work my way": a short managed block of house rules in your user CLAUDE.md.

Every change follows the same shape as switchboard/models.py: plan() says exactly what would change
and changes nothing; apply() writes it; undo() puts it back. Switchboard's record lives in
<data folder>/packs/state.json (owner-only).
"""
PACKS = ('memory', 'codemap', 'houserules')
