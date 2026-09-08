#!/usr/bin/env python3
import json, os, re, shlex, sys

MIN_LINES = int(os.environ.get("SCOUT_MIN_LINES", "350"))
SCOUT_AGENT_TYPE = os.environ.get("SCOUT_AGENT_TYPE", "scout")
READERS = {"cat", "less", "more", "head", "tail"}
# Matches -n / -c long form and -40 short form (BSD/GNU head/tail shorthand).
COUNT_FLAG = re.compile(r"^-(?:n|c|\d)")

def line_count(p):
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0

def deny(reason):
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, sys.stdout)

def deny_reason(data):
    if data.get("tool_name") != "Bash":
        return None
    if data.get("agent_type") == SCOUT_AGENT_TYPE:
        return None
    cmd = data.get("tool_input", {}).get("command", "")
    if "|" in cmd or ">" in cmd:
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None  # unparsable quoting → fail open
    if not argv or argv[0] not in READERS:
        return None
    if argv[0] in {"head", "tail"} and any(COUNT_FLAG.match(a) for a in argv[1:]):
        return None
    files = [a for a in argv[1:] if not a.startswith("-") and os.path.isfile(a)]
    big = [(f, n) for f, n in ((f, line_count(f)) for f in files) if n > MIN_LINES]
    if not big:
        return None
    desc = ", ".join(f"{f} ({n} lines)" for f, n in big)
    return (
        f"{desc} exceeds {MIN_LINES} lines. "
        f"Delegate to the scout subagent with a specific question instead."
    )

def main():
    try:
        reason = deny_reason(json.load(sys.stdin))
    except Exception:
        return
    if reason:
        deny(reason)

if __name__ == "__main__":
    main()
