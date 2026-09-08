#!/usr/bin/env python3
import json, os, sys, fnmatch

MIN_LINES = int(os.environ.get("SCOUT_MIN_LINES", "350"))
ALLOW = [g for g in os.environ.get("SCOUT_ALLOW_GLOBS", "").split(",") if g]
SCOUT_AGENT_TYPE = os.environ.get("SCOUT_AGENT_TYPE", "scout")

def deny(reason):
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, sys.stdout)

def deny_reason(data):
    """Return a deny reason, or None to allow (emit nothing)."""
    if data.get("tool_name") != "Read":
        return None
    # Bypass: scout is the worker; never block its own reads, or the design is circular.
    if data.get("agent_type") == SCOUT_AGENT_TYPE:
        return None
    inp = data.get("tool_input", {})
    path = inp.get("file_path", "")
    if not path or not os.path.isfile(path):
        return None
    if inp.get("offset") is not None or inp.get("limit") is not None:
        return None
    if any(fnmatch.fnmatch(path, g) for g in ALLOW):
        return None
    try:
        with open(path, "rb") as f:
            n = sum(1 for _ in f)
    except OSError:
        return None
    if n <= MIN_LINES:
        return None
    return (
        f"{path} is {n} lines (> {MIN_LINES}). Do not read it into the main context. "
        f"Delegate to the scout subagent with a specific question, or use Read with "
        f"offset/limit for the exact range you need to edit."
    )

def main():
    try:
        reason = deny_reason(json.load(sys.stdin))
    except Exception:
        return  # fail open: emit nothing, call proceeds
    if reason:
        deny(reason)

if __name__ == "__main__":
    main()
