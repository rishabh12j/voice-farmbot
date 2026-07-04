# V2 evaluation results

This is the V2 (phone-to-Pi intent-server + py_trees BT + event log)
hardware eval. Comparisons are against the V1 baseline reported in the
thesis interim (Gugliermo et al. metrics).

Each row records one full pass of the corpus in `tools/evaluate_v2.py`.
Treat **DBSR / SNSR / USC / Latency** as the core V1-comparable metrics
and **ELC** as a V2-specific addition.

> **Corpus history:** the original corpus was 29 utterances with V1-era
> hard-coded coordinates and species that never existed in a real map
> (herbs/carrots/strawberries — a stale V1 config). As of 2026-07-04 the
> corpus is **42 cases**: expectations resolve against the Pi's **live map**
> at run time (`@move:<species>` templates), plus three new categories —
> **refusal** (unplanted species ⇒ terminal success with zero commands),
> **hard** (realistic elderly speech: STT noise, fillers, politeness,
> hedges, delegation), and **negation** ("don't water X" ⇒ zero commands).
> DBSR numbers before/after that date are not directly comparable.

| Metric  | Meaning                                                       |
|---------|---------------------------------------------------------------|
| DBSR    | Desired Behaviour Success Rate — % of cases where the expected commands were published |
| SNSR    | Single Node Success Rate — fraction of BT leaves that returned success |
| USC     | Unsafe State Count — cases where a leaf failed with an out-of-bounds message |
| ELC     | Event Log Coverage (V2 only) — % of cases that should have logged a care event and did |
| Latency | Mean wall-clock duration_ms reported by the Pi per request    |

## V1 baseline (thesis interim, 29 utterances)

| Metric  | Value   |
|---------|---------|
| DBSR    | 96.6 %  |
| SNSR    | 98.8 %  |
| USC     | 0       |
| ELC     | n/a (no event log in V1) |
| Mean latency | 5456 ms |

## How to run

```powershell
# Terminal 1: Pi side (sim mode — no real motor moves, but the BT, event log
# and HTTP server are all real)
ssh gh1@<pi-ip>
cd ~/Rishabh_Growmate_FarmBot
source venv/bin/activate
PYTHONPATH=src:$PYTHONPATH python3 -m growmate_pi.intent_server --no-ros2 --port 8000

# Terminal 2: Windows side (this repo)
$env:PYTHONPATH = "src"
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent
```

Add `--json` to capture per-case detail for the thesis appendix:

```powershell
python tools\evaluate_v2.py --pi-url http://<pi-ip>:8000/intent --json > eval_results.json
```

Add `--no-llm` if Ollama isn't running — falls back to canned intents
(useful for Pi-only smoke tests but not for the headline V2 numbers).

Fastest loop (no Pi at all — everything local on Windows):

```powershell
# Terminal 1: sim Pi on this machine
$env:PYTHONPATH = "src"; python -m growmate_pi.intent_server --no-ros2 --port 8123
# Terminal 2: the eval (--skip-long skips the ~5-min water-everything walk)
$env:PYTHONPATH = "src;src/growmate_voice"
python tools\evaluate_v2.py --pi-url http://localhost:8123/intent --skip-long
```

## Runs

### Run 1 — 2026-07-04 · extended corpus vs the real 56-plant garden (sim)

* **Date:**             2026-07-04
* **Target:**           local sim intent server (`--no-ros2`, port 8123, Windows)
* **Garden:**           real re-planted gh1 map, 56 plants (jog-captured 2026-07-03: tomato 15, scallion 14, lettuce 8, pepper 8, marigold 6, basil 4, spearmint 1)
* **Corpus:**           42 cases (extended: +refusal/hard/negation), `--skip-long` (the deliberate 56-plant water-everything walk excluded)
* **Ollama model:**     gemma3:4b (classification grounded in live map species + memory context)
* **STT:**              n/a — the eval enters below STT (text → classify → Pi)
* **Commits:**          `b17a7f2` (map) + `2f7bea4` (corpus/fixes)

| Metric  | V2 result | V1 baseline | Δ |
|---------|-----------|-------------|---|
| DBSR    | **100.0 %** (42/42) | 96.6 % | +3.4 |
| SNSR    | 91.2 %    | 98.8 %      | −7.6 (metric artifact — see below) |
| USC     | **0**     | 0           | = |
| ELC     | **100.0 %** (n=27) | n/a | new |
| Mean latency | 9331 ms | 5456 ms | +3875 (real multi-plant sim walks now included) |

Companion app-level results, same day, same map (`tools/test_app_flows.py`,
13 scenarios driving the app in-process against the sim Pi): **FLOW 42/42,
NLP 6/6** — including session-memory pronoun resolution ("water them again"
with no plant named → new spearmint watered row in the event log) and the
embedded-UI-script `node --check` gate.

#### Regressions or surprises

* **SNSR 91.2 % is a metric artifact, not a fault:** two hard cases
  legitimately classify to `water_smart`, whose `CheckDry` condition leaves
  FAIL **by design** for moist plants (the Selector then skips watering —
  correct behaviour). Failed-by-design condition nodes under a Selector
  count against SNSR; the thesis writeup should footnote or exclude them.
* **Latency mean rose** because the corpus now contains real multi-plant
  watering walks executed to completion in sim (pump pulses run in real
  time); max was 47.6 s ("give the lettuce a drink", 8 plants).
* Found during this round (fixed in `2f7bea4`): the eval's old no-LLM
  fallback **fabricated `water_all`** — when Ollama died mid-run, every
  remaining case fired a full-garden walk. It now scores `classify_error`
  and dispatches nothing.
* Prompt-tuning side effect (fixed): adding the negation rule initially
  regressed the delegation case ("my knees hurt too much to water the
  spearmint today, can you do it" → `general_question`); an explicit
  boundary (user's inability + request = command) restored it. Evidence
  for why every prompt change re-runs the full corpus.

---

### Run 2 — TODO (hardware, gh1)

_(copy the block above; target `http://192.168.0.38:8000/intent` with the
Pi in real mode — the numbers that go in the thesis hardware section.)_

## V1 → V2 expected differences

These are not regressions; they're intentional design changes between
V1 and V2 that may show up in the numbers:

1. **Latency drops** — V1 ran the BT inside `growmate_voice` on Windows
   with mocked publishers; V2 round-trips to the Pi over HTTP. So
   `latency_ms_mean` is end-to-end wall time including the LLM call,
   the HTTP hop, the real BT tick, and the Pi-side ROS publishers.
   Even in sim mode it should be _slower_ than the V1 baseline, not
   faster, because the network hop is new.

2. **USC should stay at 0** — bounds checking moved to the Pi side
   (`farmbot_bringup` validates against the workspace before publishing),
   so anything that would have been unsafe in V1 now fails with a clear
   `out_of_bounds` message instead of being silently clamped.

3. **DBSR may dip on indirect/query cases** when the LLM picks a
   different action than V1 did (e.g. `general_question` instead of
   `check_moisture`). Both can be "correct" responses to a vague
   utterance — DBSR penalises only when the expected command substring
   doesn't appear.

4. **ELC is a new metric** — V1 had no per-plant event log, so there's
   no baseline to compare. Treat anything ≥ 95 % as "the log is wired
   end-to-end"; the cases that legitimately have no event (jog, home,
   emergency, general_question) don't count toward the denominator.
