# CLAUDE.md

All agent rules for this repository live in `AGENTS.md` — read it first; it applies to Claude
Code as well.

@AGENTS.md

## Claude Code specifics

- Project subagents: `.claude/agents/backend-architect.md` (Opus 5.5, read-only, ADRs and
  contracts) and `.claude/agents/backend-implementer.md` (Sonnet 5.5, planned BE tasks).
- Personal model routing (not committed): `.claude/settings.local.json` with
  `"model": "opusplan"` → Opus 5.5 in plan mode, Sonnet 5.5 when executing.
- Gates: `just check`.
