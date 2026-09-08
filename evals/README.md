# scout evals

Two harnesses:

1. **Hook evals** (`run.sh`) — unit-test the spillway `PreToolUse` hooks against
   stdin JSON fixtures. No model calls. Mirrors SPEC.md §10.
2. **Measurement/benchmark** (`benchmark.py`) — the CODE side of tickets SCOUT-1..5
   (SPEC.md §16). Computes the three saving numbers, worker-token capture, multi-turn
   accounting, cache-aware cost, and the break-even sweep. Runs in `--demo` mode with
   synthetic inputs; real recorded `/cost` runs drop in via `--inputs`.

```
evals/
├── run.sh                 # hook eval harness
├── read-hook-cases.json   # 18 Read-hook cases  (SPEC.md §10)
├── bash-hook-cases.json   # 20 Bash-hook cases  (SPEC.md §10)
├── benchmark.py           # SCOUT-1..5 measurement harness
├── fixtures/              # small sample source files for the benchmark demo
│   ├── sample_service.py
│   └── sample_registry.py
└── README.md
```

## Hook evals

```bash
bash evals/run.sh
```

What it does:

- Generates fixture files at **exact line counts** in a temp dir (e.g. a 500-line
  "big" file, a 350-line edge file, an 800-line `.md`, a `settings/` file), then
  `trap`-cleans them on exit.
- For each case: substitutes the fixture path into the payload, applies any per-case
  env (`SCOUT_MIN_LINES` / `SCOUT_ALLOW_GLOBS` / `SCOUT_AGENT_TYPE`) hermetically,
  pipes the stdin JSON into the correct hook
  (`python3 "$REPO_ROOT/.claude/hooks/spillway-read.py"` /
  `spillway-bash.py`), and asserts:
  - **block** case → stdout is non-empty JSON with
    `hookSpecificOutput.permissionDecision == "deny"`;
  - **allow** case → stdout is **empty**.
- Prints a per-case PASS/FAIL line and a summary; **exits nonzero if any case fails**.

Cases include the SPEC.md §10 must-haves: the **subagent bypass**
(`agent_type: scout` → allow), the **malformed-payload fail-open** (raw non-JSON
stdin → empty stdout), the **head/tail short-form** (`head -40` → allow), and the
**threshold-override-via-env** (`SCOUT_MIN_LINES` raised/lowered).

### "Run after merge" guard

The hooks live in `.claude/hooks/`, which is authored by a **parallel worker** and is
**not present in this worktree**. When `.claude/hooks/` is absent, `run.sh` prints a
clear "run after merge" message and **exits 0** (does not fail). After the branches
merge, `.claude/hooks/spillway-read.py` and `spillway-bash.py` exist at the repo root
and `bash evals/run.sh` runs the full suite for real.

The case files reference the hooks by their post-merge repo-root paths, so no edits are
needed after merge.

## Benchmark / measurement

```bash
python3 evals/benchmark.py --demo
```

Prints an illustrative report table from synthetic inputs, covering:

- **SCOUT-1** — three numbers, never one, per task:
  - `main_avoided = main_without − main_with` (the isolation win; frees the context
    window — genuine and large, but **not** the saving),
  - `total_saving = total_without − total_with`, `total = main + worker` (the honest
    figure; the worker's read cost doesn't vanish, it moves to scout),
  - `cost_delta` in $ using per-model rates (orchestrator rate on the main delta,
    worker-model rate on scout's tokens — separate effects, kept separate).
- **SCOUT-2** — worker-token accessor: uses a runtime-reported value if provided
  (tagged `measured`), else estimates `chars/4` of the files scout read (tagged
  `estimated`). Task 2 in the demo has no runtime metric, so it exercises the estimate.
- **SCOUT-3** — the three §11 tasks (orientation / small-change-blast-radius /
  bug-hunt), each with a `turns` count (≥3 follow-ups) and **cumulative** token
  accounting across turns.
- **SCOUT-4** — every result carries both raw-token cost and cache-aware cost columns.
  The ship decision uses the cache-aware total (the realistic one).
- **SCOUT-5** — `break_even(cost_model)` sweeps file line counts and reports the
  crossover where `total_with < total_without`, for a single-turn and a multi-turn
  task, compared against the 350-line default.

### Plugging in real recorded runs

There is no live Claude/Gravity to measure here, so the demo is **synthetic and
labelled ILLUSTRATIVE**. To measure for real:

1. Emit a template of the input schema:

   ```bash
   python3 evals/benchmark.py --emit-demo-inputs my-inputs.json
   ```

2. Run the three §11 tasks per SPEC.md §11 (each ≥3 follow-up turns), once with hooks
   off and once with hooks on, recording `/cost` per turn. Fill `my-inputs.json`:

   ```json
   {
     "tasks": [
       {
         "name": "1-orientation",
         "turns": 4,
         "main_without": [t0, t1, t2, t3],   // per-turn main-context tokens, hooks OFF
         "main_with":    [t0, t1, t2, t3],   // per-turn main-context tokens, hooks ON
         "worker": {
           "reported_tokens": 31000,          // scout's runtime-reported tokens, or null
           "read_chars": 128000               // chars scout read (chars/4 fallback if null)
         }
       }
     ]
   }
   ```

   `main_without` / `main_with` may be a scalar (treated as a single turn) or a
   per-turn list (summed for the cumulative multi-turn figure).

3. Report on the real numbers:

   ```bash
   python3 evals/benchmark.py --inputs my-inputs.json
   ```

The report shape is identical; only the "ILLUSTRATIVE" banner drops. Adjust the
per-model `$`/token rates and cache fractions in `CostModel` (top of `benchmark.py`)
to match the real gateway's pricing before quoting dollar figures.

## Integration checks (out of scope for these fixtures)

SPEC.md §10 also calls for ≥3 **live** end-to-end delegations against real >350-line
files to confirm the `agent_type` bypass fires in an actual subagent (the one check
that catches the v1 circular-block defect). Those are manual runtime checks, not stdin
fixtures, and are run after the hooks + subagent are wired up post-merge.
