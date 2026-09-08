#!/usr/bin/env bash
#
# evals/run.sh — hook eval harness for scout's spillway hooks (SPEC.md §10).
#
# Generates fixture files at exact line counts in a temp dir, iterates the JSON
# cases in read-hook-cases.json / bash-hook-cases.json, pipes each stdin payload
# into the matching hook, and asserts the decision:
#   - block cases -> stdout is non-empty JSON with
#     hookSpecificOutput.permissionDecision == "deny"
#   - allow cases -> stdout is empty
#
# Per-case env (SCOUT_MIN_LINES / SCOUT_ALLOW_GLOBS / SCOUT_AGENT_TYPE) is applied
# only for the case that declares it.
#
# Exit nonzero if any case fails.
#
# GUARD: the hooks live in .claude/hooks/ which is built by another worker. If that
# directory is absent (e.g. in an isolated worktree), this prints a "run after merge"
# message and exits 0 rather than failing.

set -euo pipefail

EVALS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EVALS_DIR/.." && pwd)"

READ_HOOK="$REPO_ROOT/.claude/hooks/spillway-read.py"
BASH_HOOK="$REPO_ROOT/.claude/hooks/spillway-bash.py"

if [[ ! -f "$READ_HOOK" || ! -f "$BASH_HOOK" ]]; then
  echo "scout evals: .claude/hooks/ not found in this worktree."
  echo "  expected: $READ_HOOK"
  echo "            $BASH_HOOK"
  echo "  The hooks are authored by a parallel worker. Run this AFTER merge:"
  echo "      bash evals/run.sh"
  echo "  Skipping (exit 0)."
  exit 0
fi

FIXDIR="$(mktemp -d "${TMPDIR:-/tmp}/scout-evals.XXXXXX")"
trap 'rm -rf "$FIXDIR"' EXIT

echo "scout hook evals"
echo "  repo root : $REPO_ROOT"
echo "  fixtures  : $FIXDIR"
echo

# The heavy lifting (fixture generation + case execution + assertions) is done by an
# embedded Python driver: JSON parsing and subprocess stdin piping are far more robust
# there than in pure bash. It is still launched by `bash evals/run.sh`.
python3 - "$EVALS_DIR" "$REPO_ROOT" "$FIXDIR" "$READ_HOOK" "$BASH_HOOK" <<'PYDRIVER'
import json, os, subprocess, sys

evals_dir, repo_root, fixdir, read_hook, bash_hook = sys.argv[1:6]

HOOKS = {
    "spillway-read.py": read_hook,
    "spillway-bash.py": bash_hook,
}
CASE_FILES = {
    "spillway-read.py": os.path.join(evals_dir, "read-hook-cases.json"),
    "spillway-bash.py": os.path.join(evals_dir, "bash-hook-cases.json"),
}


def make_fixture(name, spec):
    """Generate a file with exactly spec['lines'] lines and return its abs path."""
    ext = spec.get("ext", "py")
    subdir = spec.get("subdir")
    d = fixdir
    if subdir:
        d = os.path.join(fixdir, subdir)
        os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.{ext}")
    n = spec["lines"]
    with open(path, "w") as f:
        # Exactly n lines: n-1 trailing newlines written as n lines each ending in \n.
        # sum(1 for _ in open(path,'rb')) counts one per newline, so write n newlines.
        for i in range(n):
            f.write(f"# line {i + 1} of {n} in fixture {name}\n")
    # sanity: verify exact count the same way the hooks do
    with open(path, "rb") as f:
        actual = sum(1 for _ in f)
    assert actual == n, f"fixture {name}: wanted {n} lines, got {actual}"
    return path


def substitute(obj, mapping):
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace("{{" + k + "}}", v)
        return obj
    if isinstance(obj, dict):
        return {k: substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, mapping) for v in obj]
    return obj


def run_hook(hook_path, stdin_bytes, extra_env):
    env = os.environ.copy()
    # Clear the tunables so a prior case's setting never leaks; each case is hermetic.
    for k in ("SCOUT_MIN_LINES", "SCOUT_ALLOW_GLOBS", "SCOUT_AGENT_TYPE"):
        env.pop(k, None)
    env.update(extra_env or {})
    p = subprocess.run(
        ["python3", hook_path],
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return p


def is_deny(stdout_bytes):
    try:
        data = json.loads(stdout_bytes.decode("utf-8"))
    except Exception:
        return False
    hso = data.get("hookSpecificOutput", {})
    return hso.get("permissionDecision") == "deny"


total = 0
passed = 0
failures = []

for hook_name, case_file in CASE_FILES.items():
    hook_path = HOOKS[hook_name]
    with open(case_file) as f:
        doc = json.load(f)

    # Build fixtures declared for this hook.
    mapping = {}
    for fname, spec in doc.get("fixtures", {}).items():
        mapping[fname] = make_fixture(fname, spec)

    print(f"== {hook_name} ({len(doc['cases'])} cases) ==")
    for case in doc["cases"]:
        total += 1
        name = case["name"]
        expect = case["expect"]
        extra_env = case.get("env", {})

        if "raw_stdin" in case:
            stdin_bytes = substitute(case["raw_stdin"], mapping).encode("utf-8")
        else:
            payload = substitute(case["payload"], mapping)
            stdin_bytes = json.dumps(payload).encode("utf-8")

        p = run_hook(hook_path, stdin_bytes, extra_env)
        out = p.stdout

        ok = False
        detail = ""
        if p.returncode != 0:
            detail = f"hook exited {p.returncode} (must always exit 0). stderr={p.stderr.decode(errors='replace')[:200]}"
        elif expect == "block":
            ok = is_deny(out)
            if not ok:
                detail = f"expected deny JSON, got stdout={out.decode(errors='replace')[:200]!r}"
        elif expect == "allow":
            ok = (out == b"")
            if not ok:
                detail = f"expected empty stdout, got {out.decode(errors='replace')[:200]!r}"
        else:
            detail = f"unknown expect={expect!r}"

        if ok:
            passed += 1
            print(f"  PASS  {name}  [{expect}]")
        else:
            failures.append((hook_name, name, detail))
            print(f"  FAIL  {name}  [{expect}] -- {detail}")
    print()

print(f"summary: {passed}/{total} passed")
if failures:
    print("failures:")
    for hook_name, name, detail in failures:
        print(f"  - {hook_name}::{name}: {detail}")
    sys.exit(1)
sys.exit(0)
PYDRIVER
