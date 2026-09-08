# scout measurement runbook

How to produce a **real** token-cost measurement for a repo, instead of the chars/4
estimates in `benchmark.py --demo`. The output feeds `benchmark.py --inputs <file>` and
gives the honest three-number result (see `docs/measurement.md` and SPEC §11).

The principle: measure the same tasks **twice** — once with scout's hooks OFF (baseline),
once with them ON — in fresh sessions, and record `/cost` each time. Everything else is
bookkeeping.

---

## 0. Prerequisites

- The `.claude/` tree installed in the target repo (or run scout from this repo against it).
- `python3` for the harness.
- ~30–60 min. Do it on a quiet machine; background token use pollutes `/cost`.

## 1. Pick 3 representative tasks

Choose tasks that reflect real work, not toy prompts. Mirror SPEC §11:

1. **Orientation** — "explain how <feature> flows through the code" (multi-file read).
2. **Source + test pair** — "what does <module> do and what do its tests cover".
3. **Single large file** — a focused question about one >350-line file.

Write the exact prompts down. You will run each prompt **verbatim** in both phases, and
ask the **same** number of follow-up turns each time (≥3 follow-ups → 4 turns total). Any
wording difference between phases invalidates the comparison.

## 2. Baseline phase — hooks OFF

Disable scout so Claude reads files directly into the main context.

```bash
# In the target repo:
mv .claude/settings.json .claude/settings.json.off   # unregister the hooks
```

Then, **for each task**:
1. Start a **fresh** Claude Code session (no carried context).
2. Run the task prompt, then your fixed follow-up turns.
3. Run `/cost`. Record the **total tokens** for the session — this is that task's
   `main_without` (one number per turn if you can read per-turn usage; otherwise record the
   cumulative total and split evenly).
4. End the session before the next task (each task = its own session).

Restore when done:

```bash
mv .claude/settings.json.off .claude/settings.json
```

## 3. scout phase — hooks ON

With `.claude/settings.json` in place, for each task (same prompts, same turn count):
1. Fresh session.
2. Run the task; when a hook blocks a read, let Claude delegate to the `scout` subagent.
3. Run `/cost`. Record:
   - **main session tokens** → `main_with`.
   - **scout's own tokens** → `worker.reported_tokens`. If `/cost` does not break out the
     subagent, leave it `null` and instead record the total characters scout read
     (`wc -c` on the delegated files × number of delegations) as `worker.read_chars` — the
     harness will estimate at chars/4 and tag the result `estimated`.

## 4. Assemble the inputs file

Start from the template and fill in your recorded numbers:

```bash
python3 evals/benchmark.py --emit-demo-inputs /tmp/inputs.json   # writes the schema
# edit /tmp/inputs.json:
```

Per task:
- `turns` — total turns you ran (e.g. 4).
- `main_without` — array of per-turn main tokens, hooks OFF.
- `main_with` — array of per-turn main tokens, hooks ON.
- `worker.reported_tokens` — scout's measured tokens, or `null`.
- `worker.read_chars` — total chars scout read across the task (only used if
  `reported_tokens` is `null`).

## 5. Run the harness

```bash
python3 evals/benchmark.py --inputs /tmp/inputs.json
```

You get the three numbers per task:
- **main_avoid** — tokens kept out of the main context (context-window relief).
- **total_save** — the honest figure: main saved *minus* scout's read cost. Can be
  negative on small-file repos or heavy re-reads.
- **cost delta ($)** — raw and cache-aware; the ship decision uses cache-aware.

## 6. Pitfalls that corrupt the measurement

- **Different prompts between phases.** Run them verbatim. Copy-paste, don't retype.
- **Carried context.** Always a fresh session per task per phase. A warm session skews
  `main_without` down.
- **Counting only the main session.** scout's tokens are real. Always fill `worker`, or
  `total_save` collapses into shunt's flattering main-only number (SPEC §16 / SCOUT-6).
- **Ignoring caching.** Prefer per-turn numbers so the cache-aware column is meaningful;
  a cold single read overstates the without-cost.
- **Re-read count.** How often scout re-reads the corpus (once vs per follow-up) is the
  dominant lever. Keep it identical across tasks and note it — it decides whether
  `total_save` is positive.

## 7. Decision

Feed the numbers into the SPEC §11 ship criteria: enable scout for the repo only if the
**cache-aware total** improves on orientation and source+test tasks and the bug-hunt task
does not get worse. A main-context-only headline is not a valid basis (SPEC §12).
