#!/usr/bin/env python3
"""
evals/benchmark.py — measurement harness for scout (SPEC.md §11, tickets SCOUT-1..5).

This is the CODE side of the measurement. There is no live Claude/Gravity to measure
in this environment, so:

  --demo    feeds synthetic recorded inputs and prints the full report table, clearly
            labelled ILLUSTRATIVE. Use to sanity-check the math and the report shape.
  --inputs FILE
            reads real recorded inputs from a JSON file (same schema the demo emits)
            and reports on those. This is where real /cost recordings plug in later.

Tickets implemented:

  SCOUT-1  three numbers, never one:
             main_avoided = main_without - main_with
             total_saving = total_without - total_with   (total = main + worker)
             cost_delta   = $ using per-model rates (orchestrator on main, worker on scout)
           -> compute_metrics()

  SCOUT-2  worker-token accessor: use runtime-reported value if present, else estimate
           chars/4 of the read files and TAG "estimated".
           -> worker_tokens()

  SCOUT-3  task definitions for the three §11 tasks, each with a turns count (>=3
           follow-ups) and cumulative-token accounting.
           -> demo_tasks(), accumulate over turns

  SCOUT-4  each result carries both raw-token and cache-aware cost columns.
           -> CostModel.cost() with cache_write / cache_read rates

  SCOUT-5  break_even(cost_model): sweep file line counts, return the crossover where
           total_with < total_without, for single-turn and multi-turn.
           -> break_even()

Recorded-input schema (per task):
  {
    "name": str,
    "turns": int,                      # >= 3 follow-ups modelled
    "main_without": [t0, t1, ...],     # per-turn main-context tokens, hooks OFF
    "main_with":    [t0, t1, ...],     # per-turn main-context tokens, hooks ON
    "worker": {                        # scout's own spend, hooks ON
        "reported_tokens": int | null, # runtime-reported (measured) if available
        "read_chars": int              # chars of files scout read (for chars/4 estimate)
    }
  }
Per-turn lists let SCOUT-3 accumulate; a scalar is also accepted (treated as 1 turn).
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------------------
# SCOUT-4: cost model. Rates are $ per token. cache_write/cache_read let us report a
# cache-aware cost alongside the raw-token cost. The ship decision uses cache-aware.
# Demo rates are order-of-magnitude illustrative (not a price quote).
# --------------------------------------------------------------------------------------
@dataclass
class CostModel:
    # orchestrator (main session) model rates, $ / token
    main_in: float = 3.0e-6          # ~ $3 / MTok input  (illustrative Sonnet-ish)
    main_out: float = 15.0e-6        # ~ $15 / MTok output
    main_cache_write: float = 3.75e-6  # cache-write ~1.25x input
    main_cache_read: float = 0.3e-6    # cache-read  ~0.1x input
    # worker (scout, cheap model) rates, $ / token
    worker_in: float = 0.8e-6        # ~ $0.80 / MTok input (illustrative Haiku-ish)
    worker_out: float = 4.0e-6       # ~ $4 / MTok output
    # fraction of tokens that are output (rest is input). Coarse but keeps demo honest.
    out_fraction: float = 0.15
    # fraction of carried main-context input tokens served from cache when caching is on.
    cache_hit_fraction: float = 0.9

    def raw_cost(self, tokens: float, in_rate: float, out_rate: float) -> float:
        out = tokens * self.out_fraction
        inp = tokens - out
        return inp * in_rate + out * out_rate

    def cache_aware_cost(self, tokens: float, in_rate: float, out_rate: float,
                         cache_write: float, cache_read: float) -> float:
        """Same tokens, but input is split into first-write vs cached-read."""
        out = tokens * self.out_fraction
        inp = tokens - out
        cached = inp * self.cache_hit_fraction
        fresh = inp - cached
        return fresh * cache_write + cached * cache_read + out * out_rate

    def main_cost(self, tokens: float, cache_aware: bool) -> float:
        if cache_aware:
            return self.cache_aware_cost(tokens, self.main_in, self.main_out,
                                         self.main_cache_write, self.main_cache_read)
        return self.raw_cost(tokens, self.main_in, self.main_out)

    def worker_cost(self, tokens: float, cache_aware: bool) -> float:
        # Worker context is short-lived and single-turn; caching barely helps, so we
        # charge worker tokens at raw input/output either way (conservative = honest).
        return self.raw_cost(tokens, self.worker_in, self.worker_out)


# --------------------------------------------------------------------------------------
# SCOUT-2: worker-token accessor.
# --------------------------------------------------------------------------------------
@dataclass
class WorkerTokens:
    tokens: float
    source: str  # "measured" | "estimated"


def worker_tokens(reported_tokens: Optional[float], read_chars: Optional[float]) -> WorkerTokens:
    """
    SCOUT-2: prefer a runtime-reported (measured) token count; otherwise estimate via
    chars/4 of the files scout read and TAG the result 'estimated'.
    """
    if reported_tokens is not None:
        return WorkerTokens(float(reported_tokens), "measured")
    if read_chars is None:
        read_chars = 0.0
    return WorkerTokens(float(read_chars) / 4.0, "estimated")


# --------------------------------------------------------------------------------------
# SCOUT-1: the three numbers.
# --------------------------------------------------------------------------------------
@dataclass
class Metrics:
    main_without: float
    main_with: float
    worker: float
    worker_source: str
    main_avoided: float          # #1 isolation win (main-context tokens)
    total_without: float
    total_with: float
    total_saving: float          # #2 honest total-token saving
    cost_delta_raw: float        # #3a $ delta, raw tokens
    cost_delta_cache: float      # #3b $ delta, cache-aware
    cost_without_raw: float
    cost_with_raw: float
    cost_without_cache: float
    cost_with_cache: float


def compute_metrics(main_without: float, main_with: float,
                    worker: WorkerTokens, cost_model: CostModel) -> Metrics:
    """
    SCOUT-1: compute the three numbers from recorded inputs.
      1. main_avoided = main_without - main_with
      2. total_saving = (main_without + 0) - (main_with + worker)
         (without hooks there is no worker; with hooks worker cost is added back)
      3. cost_delta  = cost(without) - cost(with), per-model rates, raw and cache-aware
    A positive number is a saving; negative means delegation cost more.
    """
    main_avoided = main_without - main_with

    total_without = main_without            # no worker when hooks are off
    total_with = main_with + worker.tokens
    total_saving = total_without - total_with

    # $ — orchestrator rate on the main tokens, worker rate on scout's tokens.
    cost_without_raw = cost_model.main_cost(main_without, cache_aware=False)
    cost_with_raw = (cost_model.main_cost(main_with, cache_aware=False)
                     + cost_model.worker_cost(worker.tokens, cache_aware=False))
    cost_delta_raw = cost_without_raw - cost_with_raw

    cost_without_cache = cost_model.main_cost(main_without, cache_aware=True)
    cost_with_cache = (cost_model.main_cost(main_with, cache_aware=True)
                       + cost_model.worker_cost(worker.tokens, cache_aware=True))
    cost_delta_cache = cost_without_cache - cost_with_cache

    return Metrics(
        main_without=main_without, main_with=main_with,
        worker=worker.tokens, worker_source=worker.source,
        main_avoided=main_avoided,
        total_without=total_without, total_with=total_with,
        total_saving=total_saving,
        cost_delta_raw=cost_delta_raw, cost_delta_cache=cost_delta_cache,
        cost_without_raw=cost_without_raw, cost_with_raw=cost_with_raw,
        cost_without_cache=cost_without_cache, cost_with_cache=cost_with_cache,
    )


# --------------------------------------------------------------------------------------
# SCOUT-3: task definitions + cumulative-token accounting across turns.
# --------------------------------------------------------------------------------------
@dataclass
class Task:
    name: str
    turns: int
    main_without: list          # per-turn main tokens, hooks OFF
    main_with: list             # per-turn main tokens, hooks ON
    worker_reported: Optional[float]  # runtime-reported worker tokens (measured) or None
    worker_read_chars: float          # chars scout read, for the chars/4 fallback

    def cumulative(self):
        return sum(self.main_without), sum(self.main_with)


def task_from_record(rec: dict) -> Task:
    def as_list(v):
        return v if isinstance(v, list) else [v]
    mw = as_list(rec["main_without"])
    mith = as_list(rec["main_with"])
    worker = rec.get("worker", {})
    return Task(
        name=rec["name"],
        turns=rec.get("turns", max(len(mw), len(mith))),
        main_without=mw,
        main_with=mith,
        worker_reported=worker.get("reported_tokens"),
        worker_read_chars=worker.get("read_chars", 0.0),
    )


def evaluate_task(task: Task, cost_model: CostModel) -> Metrics:
    mw_total, mith_total = task.cumulative()
    wt = worker_tokens(task.worker_reported, task.worker_read_chars)
    return compute_metrics(mw_total, mith_total, wt, cost_model)


# --------------------------------------------------------------------------------------
# SCOUT-5: break-even sweep.
# --------------------------------------------------------------------------------------
def _synthesize_turn_tokens(file_lines: int, turns: int, tokens_per_line: float = 8.0):
    """
    Model the token flow for a file of `file_lines` lines over `turns` turns.

    WITHOUT hooks: the file is read into main context once, then carried across every
    subsequent turn (re-processed), so main tokens grow roughly linearly with turns.

    WITH hooks: scout reads the file once in its own context (worker cost), and only its
    compact bullet answer (a small fraction of the file) enters main context and is
    carried. Plus a fixed per-delegation orchestration overhead.
    """
    file_tokens = file_lines * tokens_per_line
    base_turn = 1500.0            # task prose / instructions per turn, both modes
    summary_fraction = 0.12       # scout's bullets vs full file
    deleg_overhead = 800.0        # orchestration cost to spawn/collect the subagent

    # WITHOUT: full file carried every turn after it's read.
    main_without = base_turn + file_tokens * turns
    # WITH: only the summary carried every turn, plus one-time delegation overhead.
    main_with = base_turn + file_tokens * summary_fraction * turns + deleg_overhead
    # Worker reads the whole file once (single delegation covers the session).
    worker = file_tokens
    return main_without, main_with, worker


def break_even(cost_model: CostModel, turns_variants=(1, 4),
               line_grid=range(50, 1201, 25)):
    """
    SCOUT-5: sweep file line counts; return the crossover line count where
    total_with < total_without, for each turns variant (single-turn and multi-turn).
    Returns {turns: {"crossover_lines": int|None, "sweep": [(lines, total_without, total_with)]}}
    """
    results = {}
    for turns in turns_variants:
        crossover = None
        sweep = []
        for lines in line_grid:
            mw, mith, wk = _synthesize_turn_tokens(lines, turns)
            m = compute_metrics(mw, mith, WorkerTokens(wk, "estimated"), cost_model)
            sweep.append((lines, m.total_without, m.total_with))
            if crossover is None and m.total_with < m.total_without:
                crossover = lines
        results[turns] = {"crossover_lines": crossover, "sweep": sweep}
    return results


# --------------------------------------------------------------------------------------
# Demo inputs (illustrative; SCOUT-3's three §11 tasks with >=3 follow-up turns).
# --------------------------------------------------------------------------------------
def demo_records():
    return [
        {
            "name": "1-orientation (agent-layer flow)",
            "turns": 4,
            # hooks OFF: a big module + tests + callers land in main and are carried.
            "main_without": [42000, 40000, 41000, 39000],
            # hooks ON: only scout's bullets are carried across turns.
            "main_with": [9000, 7000, 6500, 6000],
            "worker": {"reported_tokens": 31000, "read_chars": 128000},
        },
        {
            "name": "2-small-change-blast-radius (rename across services)",
            "turns": 4,
            "main_without": [30000, 33000, 31000, 32000],
            "main_with": [12000, 11000, 10500, 11500],
            # no runtime metric here -> exercises the chars/4 'estimated' fallback.
            "worker": {"reported_tokens": None, "read_chars": 96000},
        },
        {
            "name": "3-bug-hunt (why does Y fail in this trace)",
            "turns": 4,
            # bug hunt needs real code paths; delegation helps little (expected flat).
            "main_without": [28000, 30000, 29000, 31000],
            "main_with": [26000, 27000, 28000, 30000],
            "worker": {"reported_tokens": 14000, "read_chars": 52000},
        },
    ]


# --------------------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------------------
def fmt_int(x):
    return f"{int(round(x)):,}"


def fmt_usd(x):
    return f"${x:,.4f}"


def print_report(records, cost_model, illustrative: bool):
    if illustrative:
        print("=" * 78)
        print("scout benchmark — ILLUSTRATIVE (synthetic inputs; not a measurement)")
        print("=" * 78)
    else:
        print("=" * 78)
        print("scout benchmark — recorded inputs")
        print("=" * 78)
    print()

    tasks = [task_from_record(r) for r in records]

    # SCOUT-1 three-number table + SCOUT-2 worker tag + SCOUT-3 cumulative + SCOUT-4 cost.
    print("SCOUT-1/2/3/4 — three numbers per task (cumulative across turns)")
    print("-" * 78)
    header = (f"{'task':<40} {'turns':>5}  {'main_avoid':>10} {'total_save':>10} "
              f"{'worker':>8} {'src':>9}")
    print(header)
    agg = {"main_avoided": 0.0, "total_saving": 0.0}
    metrics_by_task = []
    for t in tasks:
        m = evaluate_task(t, cost_model)
        metrics_by_task.append((t, m))
        agg["main_avoided"] += m.main_avoided
        agg["total_saving"] += m.total_saving
        print(f"{t.name[:40]:<40} {t.turns:>5}  {fmt_int(m.main_avoided):>10} "
              f"{fmt_int(m.total_saving):>10} {fmt_int(m.worker):>8} {m.worker_source:>9}")
    print("-" * 78)
    print(f"{'TOTAL':<40} {'':>5}  {fmt_int(agg['main_avoided']):>10} "
          f"{fmt_int(agg['total_saving']):>10}")
    print()

    # SCOUT-4 — raw vs cache-aware cost columns.
    print("SCOUT-4 — cost delta ($): raw tokens vs cache-aware (ship decision uses cache-aware)")
    print("-" * 78)
    print(f"{'task':<40} {'raw_without':>11} {'raw_with':>10} {'cache_without':>13} {'cache_with':>11}")
    for t, m in metrics_by_task:
        print(f"{t.name[:40]:<40} {fmt_usd(m.cost_without_raw):>11} {fmt_usd(m.cost_with_raw):>10} "
              f"{fmt_usd(m.cost_without_cache):>13} {fmt_usd(m.cost_with_cache):>11}")
    print("-" * 78)
    print(f"{'task':<40} {'Δ$ raw':>11} {'Δ$ cache-aware':>24}")
    for t, m in metrics_by_task:
        print(f"{t.name[:40]:<40} {fmt_usd(m.cost_delta_raw):>11} {fmt_usd(m.cost_delta_cache):>24}")
    print()

    # SCOUT-1 honesty note.
    print("Note (SCOUT-1/6): 'main_avoid' is the isolation win (frees the context window)")
    print("but is NOT the saving. The honest figure is 'total_save' = main saved minus the")
    print("worker's read cost. Task 3 (bug hunt) is expected to stay ~flat.")
    print()

    # SCOUT-5 — break-even.
    print("SCOUT-5 — break-even sweep (crossover line count where total_with < total_without)")
    print("-" * 78)
    be = break_even(cost_model)
    for turns in sorted(be):
        c = be[turns]["crossover_lines"]
        label = "single-turn" if turns == 1 else f"multi-turn ({turns} turns)"
        if c is None:
            print(f"  {label:<24}: no crossover in swept range (delegation never wins)")
        else:
            print(f"  {label:<24}: break-even at ~{c} lines (vs the 350-line default)")
    # a few sample rows for the multi-turn sweep
    multi = max(be)
    print()
    print(f"  sample of {multi}-turn sweep (lines, total_without, total_with):")
    for lines, tw, twith in be[multi]["sweep"]:
        if lines % 100 == 0:
            print(f"    {lines:>5} lines : without={fmt_int(tw):>9}  with={fmt_int(twith):>9}"
                  + ("   <- with wins" if twith < tw else ""))
    print()
    if illustrative:
        print("(All numbers above are synthetic and for shape-checking only. Drop real")
        print(" recorded /cost inputs in via --inputs FILE to get real figures.)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="scout measurement harness (SCOUT-1..5)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true",
                   help="run with synthetic inputs; report labelled ILLUSTRATIVE")
    g.add_argument("--inputs", metavar="FILE",
                   help="JSON file of recorded task inputs (real /cost recordings)")
    ap.add_argument("--emit-demo-inputs", metavar="FILE",
                    help="write the synthetic demo inputs to FILE and exit "
                         "(a template for real recordings)")
    args = ap.parse_args(argv)

    cost_model = CostModel()

    if args.emit_demo_inputs:
        with open(args.emit_demo_inputs, "w") as f:
            json.dump({"tasks": demo_records()}, f, indent=2)
        print(f"wrote demo inputs template to {args.emit_demo_inputs}")
        return 0

    if args.inputs:
        with open(args.inputs) as f:
            doc = json.load(f)
        records = doc["tasks"] if isinstance(doc, dict) else doc
        print_report(records, cost_model, illustrative=False)
        return 0

    # default / --demo
    print_report(demo_records(), cost_model, illustrative=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
