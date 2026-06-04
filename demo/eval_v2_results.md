# V2 evaluation results

This is the V2 (phone-to-Pi intent-server + py_trees BT + event log)
hardware eval. Comparisons are against the V1 baseline reported in the
thesis interim (Gugliermo et al. metrics).

Each row records one full pass of the 29-utterance corpus from
`tools/evaluate_v2.py`. Treat **DBSR / SNSR / USC / Latency** as the
core V1-comparable metrics and **ELC** as a V2-specific addition.

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

## Runs

### Run 1 — TODO

* **Date:**            YYYY-MM-DD
* **Pi:**               gh1 @ 192.168.0.39 / farmbotdev @ 192.168.0.54
* **Mode:**             `--no-ros2` (sim) / hardware
* **Garden:**           54-plant Maynooth / 35-plant greenhouse
* **STT model:**        small.en (Day 4 default)
* **Ollama model:**     gemma3:4b
* **Whisper biasing:**  on / off
* **Soft-confirm gate:** on (Day 5)

| Metric  | V2 result | V1 baseline | Δ |
|---------|-----------|-------------|---|
| DBSR    |   ? %     | 96.6 %      |   |
| SNSR    |   ? %     | 98.8 %      |   |
| USC     |   ?       | 0           |   |
| ELC     |   ? %     | n/a         |   |
| Mean latency | ? ms | 5456 ms    |   |

#### Regressions or surprises

* _(empty — fill in after the run)_

#### Per-case anomalies

Paste any rows where `dbsr=MISS` or `elc=MISS`, with a one-line note on
why. Examples to watch for:

* **"water everything"** — soft-confirm gate may intercept; eval bypasses
  the gate by talking directly to the Pi, so this should still log a
  `watered_all`. If it doesn't, that's a Pi-side bug.
* **"the carrots need attention"** — heavily indirect; if DBSR misses,
  the issue is the LLM classifier, not the Pi.
* **Queries** (status / moisture / what's happening) — the LLM may emit
  `check_moisture` or `general_question`. Either is acceptable; the
  expected event_type comes from the actual classified action, so ELC
  follows whatever the LLM produced.
* **Emergency cases** — expected events are empty by design (the Pi
  skips logging when `req.emergency=True`); ELC shows `n/a`.

---

### Run 2 — TODO

_(copy the block above and add another entry per re-run.)_

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
