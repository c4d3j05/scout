---
name: scout
description: When and how to delegate file reading to the scout subagent instead of reading files into context.
---

# Delegating reads

Use the `scout` subagent when:
- A file is over the read threshold and you need to understand it, not edit it.
- You need to read 2+ files to answer one question (e.g. "how does the
  LangGraph agent registry resolve a node", "which views call `GravityClient`").
- You are orienting in an unfamiliar area of the repo.

Give it one specific question and the exact paths. Ask follow-ups as new calls
with the same paths — each call is independent.

Do NOT delegate when:
- Debugging — you need the actual code path, not a summary.
- Editing — use `Read` with `offset`/`limit` on the exact range.
- The file is small — overhead exceeds the saving.
- Making an architectural decision — the judgment stays with you.

Good: "In `gravity/core/orchestrator.py` and `gravity/core/registry.py`, list every
function that mutates agent state, with line numbers."
Bad: "Summarise these files."
