# scout — Measurement Methodology

Single source of truth for how the token saving is measured. `SPEC.md §11` and `§12`
reference this file; when the two disagree, this file wins on *methodology* and the spec
wins on *product decisions*. Ticket cross-refs: SCOUT-1..6 (`SPEC.md §16`).

## 0. Core principle

**The saving is context isolation, not the external/worker call.**

When scout reads a large file, that file's tokens enter *scout's* context, not the main
session's. What the main session keeps out of its window is real and is what frees room
for actual work. But those tokens do not vanish — they move to the worker. The worker
still reads the file; it still pays for it.

Therefore:

> A main-context-only percentage **overstates** the saving, because it hides the worker's
> read cost. It measures what left the main window, not what the task actually cost.

Two independent effects are in play and must never be blended into one number:

1. **Isolation** — content leaves the main context window (genuine, large).
2. **Cheaper model** — the worker can run on a cheaper model (e.g. haiku) than the
   orchestrator, so the moved tokens cost less per token.

These are separate levers. Isolation is worth something even if the worker runs on the
*same* model (it frees the window and avoids re-processing across turns, §3). The
cheaper-model discount is worth something even with no isolation. Reporting one blended
percentage conflates them and makes it impossible to reason about either. Keep them apart.

## 1. The three numbers (SCOUT-1)

Every benchmark task reports **all three** quantities. No section may cite number (a)
alone as "the saving."

Symbols:

| Symbol         | Meaning                                                            |
| -------------- | ----------------------------------------------------------------- |
| `main_without` | Main-context tokens for the task, hooks/subagent **off**          |
| `main_with`    | Main-context tokens for the task, hooks/subagent **on**           |
| `worker`       | Scout's own tokens (input + output) for the task (see §2)          |
| `orchestrator_$/tok` | Per-token price of the orchestrator model (e.g. Opus/Sonnet) |
| `worker_$/tok` | Per-token price of the worker model (e.g. haiku) — **differs** from orchestrator |

### (a) Main-context tokens avoided — the isolation win

```
main_delta = main_without − main_with
```

This is the isolation win: tokens that never entered the main window. It is genuine and
large. It is **not** the saving on its own — it ignores that the worker paid to read the
same content. Report it, but never headline on it (SCOUT-6).

### (b) Total tokens — the honest number

```
total_without = main_without                 # no worker when hooks are off
total_with    = main_with + worker
total_saving  = 1 − (total_with / total_without)
```

`total_saving` is the honest token saving. Because `worker > 0`, it is always smaller than
the main-only figure `main_delta / main_without`. That gap is exactly the worker's read
cost that the main-only number hides.

### (c) Cost delta ($) — the money number

Isolation and cheaper-model pricing are separate effects (§0), so cost is computed with
per-model rates, not one blended rate:

```
cost_without = main_without × orchestrator_$/tok
cost_with    = main_with    × orchestrator_$/tok  +  worker × worker_$/tok
cost_delta   = cost_without − cost_with
```

Equivalently, expressed as the two levers:

```
cost_delta = main_delta × orchestrator_$/tok      # isolation, priced at orchestrator rate
           − worker     × worker_$/tok            # worker's read, priced at worker rate
```

Use **symbolic** rates in the spec and templates; plug in the real published per-token
prices for the specific orchestrator and worker models at measurement time (haiku and the
orchestrator differ by roughly an order of magnitude, which is a large part of why (c) can
be favourable even when (b) is modest). Do not hard-code a dollar figure into the spec.

`cost_delta > 0` means the task got cheaper in dollars. Note it is possible for
`total_saving` to be small (or even negative) while `cost_delta` is positive, purely
because the worker's tokens are priced at the cheaper worker rate. Report both; neither
substitutes for the other.

## 2. Worker-token capture (SCOUT-2, doc side)

Numbers (b) and (c) are unmeasurable without `worker`. Capture it in this priority order:

1. **Preferred — runtime-reported subagent usage.** Read scout's input+output tokens from
   the runtime's own accounting for the delegation (agent result metadata / transcript
   usage that Claude Code surfaces for a subagent run). This is a **measured** number.
2. **Fallback — `chars/4` estimate.** If the runtime does not expose per-subagent usage,
   estimate `worker ≈ (sum of chars in the files scout read) / 4` (shunt's estimator) and
   label the number **"estimate"**, never "measured."

Every reported `worker` value carries a tag: **`measured`** or **`estimated`**. A total or
cost derived from an estimated worker count inherits the `estimated` tag. The harness side
of this (wiring `evals/run.sh --benchmark` to emit the tagged count) is SCOUT-2 code work
and is owned by the harness worker; this document defines only *what* to capture and *how
to label* it.

## 3. Multi-turn requirement (SCOUT-3)

**Single reads understate the isolation win.** The value of isolation is not a one-shot
read — it is *avoided re-processing of the same file across turns*. In a normal session,
a large file read in turn 1 keeps costing tokens as it rides along in the context of
turns 2, 3, 4… (unless fully cache-read, see §4). With scout, it was never in the main
window, so it costs nothing on later turns.

Requirement: each benchmark task runs a **realistic session with ≥3 follow-up turns** that
would otherwise re-include the file(s). Record **cumulative** tokens across the whole
session — `main_without`, `main_with`, and `worker` are session totals, not single-read
figures. A single-shot benchmark measures the weakest version of scout's value and must
not be used as the basis for a ship decision.

## 4. Caching treatment (SCOUT-4)

Prompt caching changes the picture: carried context is cheap on cache-reads, which shrinks
the isolation win (the re-processing scout avoids was partly discounted anyway). Ignoring
caching **overstates** the saving; assuming zero cross-turn reuse is unrealistic.

Report each task **two ways**:

1. **Raw tokens** — the token counts of §1, cache-agnostic.
2. **Cache-aware cost** — dollar cost with cache-write and cache-read priced separately.
   Tokens written to cache pay the cache-write rate once; tokens served from cache on
   later turns pay the (much lower) cache-read rate. Apply this to both the `without`
   (main session carrying the file across turns) and `with` (scout's own reads, and any
   carried summary) legs.

**The headline uses the cache-aware total.** Raw tokens are reported alongside for
transparency and to expose how much of the apparent saving is caching versus isolation,
but the ship gate reads the cache-aware total-cost number. A results table therefore
carries a caching column/note distinguishing raw-token saving from cache-aware cost
saving.

## 5. Break-even (SCOUT-5)

**Break-even** is the file line-count at which delegation stops costing more than a direct
read:

```
break_even = smallest file size (lines) at which  total_with < total_without
```

Below break-even, the worker read plus subagent orchestration overhead exceeds the cost of
just reading the file directly, so delegation is a net loss on total tokens. Above it,
delegation wins.

Break-even must be found **empirically** by sweeping file sizes, and reported for **two
regimes**:

- **Single-turn** — one read, one answer. Overhead is worst-case here, so break-even is
  highest.
- **Multi-turn** (≥3 follow-ups, §3) — the avoided re-processing pushes break-even
  *lower*, because the direct-read cost recurs each turn while the delegation overhead is
  paid roughly once.

Compare both crossovers to the **350-line default** (`SCOUT_MIN_LINES`, `SPEC.md §8`). The
350 default is inherited from shunt, not measured. The experiment either confirms 350 with
evidence or supplies a measured line count to replace it. `SPEC.md §8`'s threshold guidance
references this experiment.

## 6. Worked example

One hypothetical orientation task run through all three numbers, to show why main-only ≠
total. **Illustrative token counts; symbolic rates.** Numbers are round for clarity, not
measured.

Setup: a 900-line service module (~24K tokens). Orientation task, session of 4 turns
(1 initial + 3 follow-ups, §3). Rates symbolic: let orchestrator = `R_o` $/tok,
worker (haiku) = `R_w` $/tok, with `R_w ≈ R_o / 10` (haiku is ~an order cheaper).

**Without scout (hooks off).** The file is read into the main context in turn 1 and rides
along through turns 2–4.

- Turn 1: 24K (file) + 6K (surrounding task) = 30K
- Turns 2–4: file re-carried each turn, ~24K + ~4K work each = 3 × 28K = 84K
- `main_without = total_without = 114K`, `worker = 0`

**With scout (hooks on).** Scout reads the file in its own context and returns ~1.5K of
bullets. The bullets ride along in the main context; the 24K file never does.

- Main, turn 1: 1.5K (bullets) + 6K (task) = 7.5K
- Main, turns 2–4: 1.5K (bullets) + ~4K work each = 3 × 5.5K = 16.5K
- `main_with = 7.5K + 16.5K = 24K`
- Worker: reads 24K once, plus ~1K output = `worker = 25K` (`measured` if the runtime
  surfaces it; else `estimated` via chars/4)

Now the three numbers:

**(a) Main-context tokens avoided.**
```
main_delta = 114K − 24K = 90K        →  main-only "saving" = 90K / 114K = 79%
```
This 79% is the flattering figure. It is real as an isolation statement — 90K never touched
the main window — but it is **not** the task's saving.

**(b) Total tokens.**
```
total_without = 114K
total_with    = main_with + worker = 24K + 25K = 49K
total_saving  = 1 − 49K/114K = 57%
```
The honest saving is **57%, not 79%**. The 22-point gap is precisely the worker's 25K read
cost that the main-only number hid.

**(c) Cost delta ($), symbolic rates.**
```
cost_without = 114K × R_o
cost_with    = 24K × R_o + 25K × R_w
             = 24K × R_o + 25K × (R_o/10)   = 26.5K × R_o
cost_delta   = 114K·R_o − 26.5K·R_o = 87.5K × R_o     →  cost saving ≈ 77%
```
In dollars the saving is **~77%** — much closer to the flattering 79% than the honest 57%
token figure — but for a *different, legitimate* reason: the worker's tokens are priced at
the cheaper haiku rate. That is the cheaper-model lever (§0), reported separately from
isolation, exactly as intended. Blending them into one "77% saving" headline would hide
that most of the gap between 57% and 77% is model pricing, not isolation.

Takeaway: **main-only (79%) ≠ total tokens (57%) ≠ cost (77%)**, and each answers a
different question. Report all three, headline on total tokens + cost (cache-aware, §4),
never on main-only (SCOUT-6).
