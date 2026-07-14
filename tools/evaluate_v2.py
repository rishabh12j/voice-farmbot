"""V2 evaluation harness — metrics from Gugliermo et al. (2024, "Evaluating
behavior trees", RAS 178:104714; names + intent verified against the full
text), operationalized for the phone-to-Pi pipeline as defined below. Two
disclosed deviations: their USC is a normalized frequency of ENTERED unsafe
states (nU/nT) while ours is a raw per-case count of guard-BLOCKED attempts
(both are 0 for this system — nothing is ever entered, by construction), and
their SNSR is per-node while ours pools all leaves. Attribution audit:
documentation/eval/dossier_01_gugliermo_metrics.md §9 (emergent-eval branch).

Differs from the legacy ``growmate-bt/evaluate_bt.py`` in three ways:

1. Source of truth for the tree is the **Pi's** response, not a local dict
   built by ``AICore``. We measure what actually happened on the Pi
   (commands_published, node_results), which is the real product.
2. Uses ``AICore`` only for intent classification — same LLM call as the
   original, but the tree is constructed and executed remotely.
3. The Pi must be running in sim mode (``--no-ros2``) for the eval to be
   meaningful — otherwise we'd be ringing a wet pump 29 times.

Usage::

    # 1. start the Pi in sim mode in another terminal:
    #    PYTHONPATH=src python3 -m growmate_pi.intent_server --no-ros2
    # 2. run the eval:
    PYTHONPATH=src python3 tools/evaluate_v2.py --pi-url http://localhost:8000/intent

Metrics:

* **DBSR**  Desired Behaviour Success Rate — % cases where every
  expected command substring appears in Pi's ``commands_published``.
* **SNSR**  Single Node Success Rate — fraction of leaf nodes that
  finished with ``success`` across all trees executed.
* **USC**   Unsafe State Count — number of cases where Pi reported
  ``failure`` due to a bounds violation (we look for "out of bounds" in
  the node messages).
* **ELC**   Event Log Coverage (Day 12) — % cases where every expected
  per-plant event row (watered / sensed / photographed / …) actually
  landed in the Pi's SQLite event log. Cases with no expected events
  (jog, home, emergency, general_question) don't count.
* **Latency** Mean wall-clock duration_ms reported by Pi per request.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: python3 -m pip install --user httpx")
    sys.exit(2)


# ---- test cases -------------------------------------------------------------
#
# Expected-command entries are either literal substrings ("H_0", "D_W_1") or
# the runtime template "@move:<species>" — resolved against the Pi's live
# /plants/by_species just before the run, so the corpus never hard-codes
# coordinates that rot when the garden is re-planted. (The V1 corpus did
# exactly that, and referenced species that never existed in any real map —
# herbs / carrots / strawberries came from a stale V1 config.)
#
# Category "refusal": species NOT in the live map. The grounded classifier +
# BT must produce a clean spoken refusal with ZERO robot commands (Q-design).
# Species below exist in the repo's 56-plant sim map (the real re-planted gh1
# garden); small beds (spearmint=1, basil=4, marigold=6) keep the real-time
# sim watering walks short.

TestCase = Tuple[str, str, List[str], str, str]
# (utterance, expected_type, expected_command_substrings, description, category)

TEST_CASES: List[TestCase] = [
    # Direct robot commands
    ("water the spearmint",    "robot_command", ["@move:spearmint", "D_W_1"], "Direct water", "direct"),
    # Named moves resolve to the species' config-representative position (by
    # design), not an active_map plant — assert an absolute move happened.
    ("move to the lettuce",    "robot_command", ["M "],                 "Direct move",  "direct"),
    ("go home",                "robot_command", ["H_0"],                "Go home",      "direct"),
    ("turn on the lights",     "robot_command", ["D_L_1"],              "Light on",     "direct"),
    ("water the marigold",     "robot_command", ["@move:marigold", "D_W_1"], "Water small bed", "direct"),
    ("take a photo",           "robot_command", ["I_1"],                "Photo",        "direct"),
    ("check moisture levels",  "robot_command", ["P_9"],                "Moisture",     "direct"),
    ("move to the scallions",  "robot_command", ["M "],                 "Move scallion","direct"),
    ("scan for weeds",         "robot_command", ["I_4"],                "Weed scan",    "direct"),
    ("turn off the lights",    "robot_command", ["D_L_0"],              "Light off",    "direct"),

    # Indirect (LLM must infer intent). Assert motion to the right species;
    # pump commands aren't asserted here because a water_smart classification
    # is also legitimate ("seems dry") and may skip moist plants.
    ("the spearmint seems dry",    "robot_command", ["@move:spearmint"], "Indirect water",   "indirect"),
    ("the peppers look thirsty",   "robot_command", ["@move:pepper"],    "Indirect water 2", "indirect"),
    ("give the lettuce a drink",   "robot_command", ["@move:lettuce"],   "Informal water",   "indirect"),
    ("take care of the marigold",  "robot_command", ["@move:marigold"],  "Vague command",    "indirect"),

    # Queries (check_sensor mounts the probe, moves, reads the soil pin)
    ("how are the tomatoes looking today","robot_query",["D_S_C"], "Status query","query"),
    ("is the soil moist enough",   "robot_query",   ["P_9"],       "Moisture query","query"),
    ("check on the lettuce for me","robot_query",   ["D_S_C"],     "Check query",  "query"),

    # General knowledge
    ("when should I plant basil",        "general", [], "Planting advice", "general"),
    ("how often should I water tomatoes","general", [], "Watering advice", "general"),
    ("what vegetables grow well in spring","general",[],"Seasonal advice", "general"),

    # Emergency
    ("stop",            "emergency", ["e"], "E-stop",   "emergency"),
    ("halt",            "emergency", ["e"], "E-halt",   "emergency"),
    ("emergency stop",  "emergency", ["e"], "E-phrase", "emergency"),
    ("freeze",          "emergency", ["e"], "E-freeze", "emergency"),

    # Multi-step
    ("water the spearmint and then go home","robot_command",["@move:spearmint","D_W_1","H_0"],"Multi","multi"),
    ("check on the marigold then water them","robot_command",["D_S_C"],   "Multi q+c","multi"),

    # Safety edge — the real water_all walks EVERY plant with verified pump
    # cycles (P_4 only fires on an empty map), so this is the long case.
    ("water everything","robot_command",["D_W_1"],"Water all (long walk)","safety"),

    # Refusals — species NOT in the live map. Zero commands, clean refusal.
    ("water the bananas",       "refusal", [], "Unknown species",   "refusal"),
    ("water the strawberries",  "refusal", [], "V1 ghost species",  "refusal"),
    ("move to the carrots",     "refusal", [], "Unknown move",      "refusal"),
    ("water the herbs",         "refusal", [], "V1 ghost species 2","refusal"),

    # Hard cases — realistic elderly speech: STT noise, fillers, politeness
    # wrappers, indirection. The classifier must still land action + target.
    ("walter the spearmint",                              "robot_command", ["@move:spearmint"], "STT noise water->Walter", "hard"),
    ("could you please water the marigold for me dear",   "robot_command", ["@move:marigold"],  "Politeness wrapper",      "hard"),
    ("um can you uh water the spearmint please",          "robot_command", ["@move:spearmint"], "Fillers",                 "hard"),
    ("the marigold looks a bit sad maybe give it some water", "robot_command", ["@move:marigold"], "Hedged indirect",      "hard"),
    ("i think the pepper bed could use a drink",          "robot_command", ["@move:pepper"],    "Vague informal",          "hard"),
    ("pop over to the lettuce and see how it's doing",    "robot_query",   ["D_S_C"],           "Informal check",          "hard"),
    ("my knees hurt too much to water the spearmint today can you do it", "robot_command", ["@move:spearmint"], "Elderly context", "hard"),
    ("give the marigold a little sprinkle would you",     "robot_command", ["@move:marigold"],  "Colloquial water",        "hard"),
    ("it's getting dark put the lights on love",          "robot_command", ["D_L_1"],           "Colloquial lights",       "hard"),
    ("take a photo for me love",                          "robot_command", ["I_1"],             "Colloquial photo",        "hard"),

    # Negation — saying a plant's name must NOT trigger action on it.
    # Scored like refusals: terminal success with ZERO robot commands.
    ("don't water the tomatoes",          "negation", [], "Direct negation",  "negation"),
    ("no need to water anything today",   "negation", [], "Blanket negation", "negation"),
]


# Day 7 / 12: which intent actions log a row in the Pi's event_log. Mirrors
# growmate_pi.intent_server._INTENT_EVENT_MAP — if that map changes, this
# must too. Anything not listed here doesn't write a row.
_ACTION_TO_EVENT: dict = {
    "water":          "watered",
    "water_all":      "watered_all",
    "check_sensor":   "sensed",
    "check_moisture": "moisture_check",
    "photo":          "photographed",
    "panorama":       "panorama",
    "scan_weeds":     "weed_scan",
    "light_on":       "lights_on",
    "light_off":      "lights_off",
}


@dataclass
class CaseResult:
    utterance: str
    expected_commands: List[str]
    category: str
    pi_status: str
    commands_published: List[str] = field(default_factory=list)
    node_count: int = 0
    nodes_success: int = 0
    nodes_failure: int = 0
    out_of_bounds: bool = False
    duration_ms: int = 0
    error: Optional[str] = None
    dbsr_pass: bool = False
    # Day 12: event-log tracking
    expected_event_types: List[str] = field(default_factory=list)
    observed_event_types: List[str] = field(default_factory=list)
    elc_applicable: bool = False
    elc_pass: bool = False
    # Extended-corpus scoring: forbidden command fragments that WERE emitted
    # (negation / self-correction cases). Non-empty => dbsr_pass is False.
    forbidden_hit: List[str] = field(default_factory=list)


# ---- intent generation ------------------------------------------------------


def _build_intent_payload(
    utterance: str,
    category: str,
    classifier,
    live_plants: Optional[List[str]] = None,
) -> dict:
    """Use AICore (when available) to classify; otherwise hand-craft.

    ``live_plants`` mirrors the app exactly: the classifier is grounded in the
    Pi's live active_map species (app._known_species_in_garden()), so the eval
    measures the same pipeline the user talks to — not an ungrounded variant.

    The harness gracefully degrades when Ollama isn't running so we can
    still smoke-test the Pi side without a live LLM.
    """
    if classifier is not None:
        if classifier.is_available():
            intents = classifier._classify(utterance, live_plants=live_plants) or []
            if intents:
                return {
                    "intents": intents,
                    "raw_text": utterance,
                    "emergency": category == "emergency",
                    "client_id": "evaluate_v2",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "schema_version": "1.0.0",
                }
        # LLM was REQUESTED but is down / returned nothing: never fabricate an
        # intent. The old fallback hand-crafted water_all here — when Ollama
        # died mid-run, every remaining case fired a full-garden watering walk.
        # A classification outage must score as a miss, not water the world.
        return None

    # --no-llm (Pi-only smoke test): canned intents so the Pi still does
    # something measurable. Emergency stops the BT; general asks a question;
    # everything else is a deliberate water_all exercising the long path.
    if category == "emergency":
        intents = [{"action": "emergency_stop", "target": None, "response": "Stop."}]
    elif category == "general":
        intents = [{"action": "general_question", "target": None,
                    "question": utterance, "response": "Looking that up."}]
    else:
        intents = [{"action": "water_all", "target": None, "response": "Watering."}]

    return {
        "intents": intents,
        "raw_text": utterance,
        "emergency": category == "emergency",
        "client_id": "evaluate_v2",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "schema_version": "1.0.0",
    }


def _expected_events_for_intents(intents: list, is_emergency: bool) -> List[str]:
    """Which event_types we expect to land in the Pi log after this batch.

    Emergency batches are short-circuited (intent_server skips logging on
    ``req.emergency=True``) — return empty so the eval doesn't penalise them.
    """
    if is_emergency:
        return []
    out: List[str] = []
    for i in intents:
        ev = _ACTION_TO_EVENT.get(i.get("action") or "")
        if ev:
            out.append(ev)
    return out


def _fetch_event_log_tail(client, base_url: str, limit: int = 20) -> List[dict]:
    """Pull the most recent ``limit`` rows from the Pi's event log.

    Returns a list of event dicts (newest first), or an empty list on any
    failure — the eval shouldn't crash if the log endpoint is briefly
    unresponsive between cases.
    """
    try:
        r = client.get(f"{base_url}/events?limit={int(limit)}")
        r.raise_for_status()
        return r.json().get("events", []) or []
    except Exception:
        return []


def _try_import_ai_core():
    try:
        from growmate_voice.ai_core import AICore  # type: ignore[import-not-found]
        cfg = REPO_ROOT / "src" / "growmate_voice" / "config" / "farmbot.yaml"
        return AICore(config_path=str(cfg))
    except Exception as exc:
        print(f"[eval] AICore unavailable ({exc}); using fallback intents")
        return None


# ---- main eval loop ---------------------------------------------------------


def _fetch_live_species(client, base_url: str) -> List[str]:
    """Species list from the Pi's live active_map (grounds the classifier)."""
    try:
        r = client.get(f"{base_url}/plants/species")
        r.raise_for_status()
        return r.json().get("species") or []
    except Exception:
        return []


def _resolve_expected(client, base_url: str, exp_cmds: List[str],
                      cache: dict) -> List[str]:
    """Expand "@move:<species>" templates into live-map "M <x> <y>" substrings.

    Uses the FIRST matching plant's coordinates; the water/move trees walk
    every match, so any plant of the species appears in commands_published.
    Falls back to plain "M " (any move) if the species lookup fails, so a
    transient HTTP hiccup degrades the assertion instead of crashing the run.
    """
    out: List[str] = []
    for c in exp_cmds:
        if not c.startswith("@move:"):
            out.append(c)
            continue
        species = c.split(":", 1)[1]
        if species not in cache:
            try:
                r = client.get(f"{base_url}/plants/by_species/{species}")
                r.raise_for_status()
                plants = r.json().get("plants") or []
            except Exception:
                plants = []
            cache[species] = plants
        plants = cache[species]
        if plants:
            p = plants[0]
            out.append(f"M {float(p['x'])} {float(p['y'])}")
        else:
            out.append("M ")
    return out


def _await_terminal(client, base_url: str, reply: dict,
                    poll_s: float = 2.0, timeout_s: float = 600.0) -> dict:
    """Follow an async /intent reply ("running" + task_id) to its terminal
    result via /intent_status. Long trees (multi-plant waters) return
    immediately with a task_id; the commands/tree only exist in the terminal
    response. Returns the original reply if it's already terminal."""
    if reply.get("status") != "running" or not reply.get("task_id"):
        return reply
    task_id = reply["task_id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        try:
            r = client.get(f"{base_url}/intent_status/{task_id}")
            r.raise_for_status()
            status = r.json()
        except Exception:
            continue
        if status.get("status") != "running":
            return status
    reply["error"] = f"async task {task_id} still running after {timeout_s:.0f}s"
    return reply


def load_external_corpus(path: str, sample: Optional[int] = None,
                         seed: int = 42):
    """Load the extended JSON corpus (tools/corpus) into the harness shape.

    Returns (cases, forbidden) where cases are the same 5-tuples as
    TEST_CASES — via the corpus's own load_corpus.py loader — and forbidden
    maps utterance -> command fragments that must NOT be emitted (negation /
    self-correction cases). ``sample`` draws a category-proportional,
    seed-deterministic subset for smoke runs.
    """
    corpus_dir = Path(path).resolve().parent
    sys.path.insert(0, str(corpus_dir))
    from load_corpus import load_cases, load_forbidden  # type: ignore
    cases = load_cases(path)
    forbidden = load_forbidden(path)
    if sample and sample < len(cases):
        import random as _random
        rng = _random.Random(seed)
        by_cat: Dict[str, list] = {}
        for c in cases:
            by_cat.setdefault(c[4], []).append(c)
        picked = []
        for _cat, group in sorted(by_cat.items()):
            n = max(1, round(sample * len(group) / len(cases)))
            picked.extend(rng.sample(group, min(n, len(group))))
        rng.shuffle(picked)
        cases = picked[:sample]
    return cases, forbidden


def run_eval(pi_url: str, use_llm: bool, http_timeout_s: float,
             skip_long: bool = False, cases=None,
             forbidden: Optional[Dict[str, list]] = None) -> List[CaseResult]:
    classifier = _try_import_ai_core() if use_llm else None
    results: List[CaseResult] = []
    cases = cases if cases is not None else TEST_CASES
    forbidden = forbidden or {}

    # Day 12: the /events endpoint lives on the same Pi but at a different path
    # — strip the /intent suffix so we can query the log between cases.
    base_url = pi_url.rsplit("/intent", 1)[0]
    species_cache: dict = {}

    with httpx.Client(timeout=http_timeout_s) as client:
        # A previous run (or a real session) may have left the estop latched —
        # the Pi then refuses every /intent with an instant failure. Start
        # from a released state so case 1 isn't judged on stale latch state.
        try:
            client.post(f"{base_url}/reset_estop")
        except Exception:
            pass

        live_species = _fetch_live_species(client, base_url)
        print(f"# grounded on live map species: {live_species or '(none — config fallback)'}")
        # Snapshot the highest event id BEFORE the run so we can find rows
        # added by this eval pass without touching the prod log.
        pre_run_tail = _fetch_event_log_tail(client, base_url, limit=1)
        last_seen_id = pre_run_tail[0].get("id", 0) if pre_run_tail else 0

        for utterance, exp_type, exp_cmds, desc, category in cases:
            if skip_long and category == "safety":
                continue  # the 54-plant water_all walk (~5 min sim) — full runs only
            exp_cmds = _resolve_expected(client, base_url, exp_cmds, species_cache)
            payload = _build_intent_payload(utterance, category, classifier,
                                            live_plants=live_species)
            if payload is None:
                # LLM requested but unavailable — an honest miss, no dispatch.
                results.append(CaseResult(
                    utterance=utterance, expected_commands=exp_cmds,
                    category=category, pi_status="classify_error",
                    error="classifier unavailable / returned no intents",
                ))
                continue
            expected_events = _expected_events_for_intents(
                payload.get("intents") or [], category == "emergency"
            )

            t0 = time.monotonic()
            try:
                r = client.post(pi_url, json=payload)
                r.raise_for_status()
                reply = r.json()
                # Long trees run async: follow to the terminal result, which is
                # where commands_published / tree actually live.
                reply = _await_terminal(client, base_url, reply)
            except Exception as exc:
                elapsed = int((time.monotonic() - t0) * 1000)
                results.append(
                    CaseResult(
                        utterance=utterance,
                        expected_commands=exp_cmds,
                        category=category,
                        pi_status="error",
                        duration_ms=elapsed,
                        error=str(exc),
                        expected_event_types=expected_events,
                    )
                )
                continue

            commands = reply.get("commands_published", []) or []
            tree = reply.get("tree") or {}
            nodes = tree.get("node_results", []) or []
            successes = sum(1 for n in nodes if n.get("status") == "success")
            failures = sum(1 for n in nodes if n.get("status") == "failure")
            oob = any(
                "out of bounds" in (n.get("message") or "").lower() for n in nodes
            )

            no_action = (category in ("refusal", "negation", "out_of_scope")
                         or exp_type in ("refusal", "negation", "out_of_scope"))
            if no_action:
                # Pass only on a CLEAN decline: terminal success AND zero robot
                # commands. Refusal = species not planted; negation = the user
                # explicitly said NOT to act; out_of_scope = not a garden task
                # ("open the door") — acting on any of these is the worst kind
                # of confidently-wrong.
                dbsr_pass = (reply.get("status") == "success") and not commands
            elif exp_cmds:
                dbsr_pass = all(any(c in cmd for cmd in commands) for c in exp_cmds)
            else:
                dbsr_pass = reply.get("status") in ("success", "partial")

            # Extended-corpus negative assertions: fragments that must NOT
            # appear in the emitted commands (negation / self-correction —
            # "water the tomatoes… no wait, the basil" must not water
            # tomatoes even though it legitimately waters basil).
            forb = forbidden.get(utterance) or []
            forbidden_hit = [f for f in forb
                             if any(f in cmd for cmd in commands)]
            if forbidden_hit:
                dbsr_pass = False

            # Day 12: pull recent log rows added since the last snapshot and
            # check expected event_types are present. Only counts when this
            # case was supposed to log something AND the Pi reported success
            # — a failed tree shouldn't be marked as a logging miss.
            observed_events: List[str] = []
            elc_applicable = bool(expected_events) and reply.get("status") in ("success", "partial")
            elc_pass = False
            if expected_events:
                fresh_tail = _fetch_event_log_tail(
                    client, base_url, limit=max(5, len(expected_events) + 5)
                )
                new_rows = [e for e in fresh_tail if e.get("id", 0) > last_seen_id]
                if new_rows:
                    last_seen_id = max(e.get("id", last_seen_id) for e in new_rows)
                observed_events = [e.get("event_type") for e in new_rows]
                if elc_applicable:
                    elc_pass = all(et in observed_events for et in expected_events)

            # The Pi LATCHES estop: after an emergency case every subsequent
            # /intent is refused until the operator resets. Release it so the
            # remaining cases are measured on their own merits (the latch
            # behaviour itself is covered by the app flow suite).
            if category == "emergency":
                try:
                    client.post(f"{base_url}/reset_estop")
                except Exception:
                    pass

            results.append(
                CaseResult(
                    utterance=utterance,
                    expected_commands=exp_cmds,
                    category=category,
                    pi_status=reply.get("status", "unknown"),
                    commands_published=commands,
                    node_count=len(nodes),
                    nodes_success=successes,
                    nodes_failure=failures,
                    out_of_bounds=oob,
                    duration_ms=int(reply.get("duration_ms", 0)),
                    dbsr_pass=bool(dbsr_pass),
                    expected_event_types=expected_events,
                    observed_event_types=observed_events,
                    elc_applicable=elc_applicable,
                    elc_pass=elc_pass,
                    forbidden_hit=forbidden_hit,
                )
            )
    return results


def _summarise(results: List[CaseResult]) -> dict:
    total = len(results)
    dbsr_pass = sum(1 for r in results if r.dbsr_pass)
    total_nodes = sum(r.node_count for r in results)
    total_succ = sum(r.nodes_success for r in results)
    usc = sum(1 for r in results if r.out_of_bounds)
    latencies = [r.duration_ms for r in results if r.duration_ms > 0]
    # Day 12: ELC denominator is only the cases that should have logged.
    elc_applicable = [r for r in results if r.elc_applicable]
    elc_pass = sum(1 for r in elc_applicable if r.elc_pass)
    return {
        "n_cases": total,
        "DBSR": round(dbsr_pass / max(total, 1) * 100, 1),
        "SNSR": round(total_succ / max(total_nodes, 1) * 100, 1),
        "USC": usc,
        "ELC": round(elc_pass / max(len(elc_applicable), 1) * 100, 1) if elc_applicable else None,
        "ELC_n_applicable": len(elc_applicable),
        "latency_ms_mean": round(statistics.mean(latencies), 0) if latencies else 0,
        "latency_ms_max": max(latencies) if latencies else 0,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GrowMate V2 BT evaluation")
    ap.add_argument("--pi-url", default="http://localhost:8000/intent")
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="skip AICore classification; use canned intents (Pi-only smoke test)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="per-request HTTP timeout (seconds)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of a table summary",
    )
    ap.add_argument(
        "--skip-long",
        action="store_true",
        help="skip the ~5-min water-everything sim walk (fast iteration runs)",
    )
    ap.add_argument(
        "--corpus",
        metavar="JSON",
        default=None,
        help="run the extended JSON corpus (tools/corpus/"
             "growmate_test_corpus.json) instead of the built-in 43 cases",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=None,
        help="with --corpus: category-proportional deterministic subset size "
             "(smoke runs; full corpus is 2000 cases ~hours)",
    )
    args = ap.parse_args(argv)

    print(f"# V2 eval target: {args.pi_url}")
    cases = forbidden = None
    if args.corpus:
        cases, forbidden = load_external_corpus(args.corpus, sample=args.sample)
        print(f"# external corpus: {len(cases)} cases"
              + (f" (sampled from 2000, seed 42)" if args.sample else ""))
    results = run_eval(args.pi_url, use_llm=not args.no_llm,
                       http_timeout_s=args.timeout, skip_long=args.skip_long,
                       cases=cases, forbidden=forbidden)
    summary = _summarise(results)
    # Per-category DBSR breakdown — the analysis view the extended corpus is
    # for (difficulty/category live in each case row of the JSON output).
    by_cat: Dict[str, List[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)
    summary["DBSR_by_category"] = {
        cat: round(100.0 * sum(r.dbsr_pass for r in rs) / len(rs), 1)
        for cat, rs in sorted(by_cat.items())
    }
    summary["forbidden_violations"] = sum(bool(r.forbidden_hit) for r in results)

    if args.json:
        print(json.dumps({"summary": summary, "cases": [r.__dict__ for r in results]}, indent=2))
        return 0

    print(f"\n{'category':<10} {'utterance':<42} {'status':<8} {'dbsr':<5} {'elc':<5} {'ms':<6}")
    print("-" * 85)
    for r in results:
        flag = "OK" if r.dbsr_pass else "MISS"
        if not r.elc_applicable:
            elc = "n/a"
        elif r.elc_pass:
            elc = "OK"
        else:
            elc = "MISS"
        print(f"{r.category:<10} {r.utterance[:40]:<42} {r.pi_status:<8} {flag:<5} {elc:<5} {r.duration_ms:<6}")

    print(f"\nSummary: {summary}")
    return 0 if summary["DBSR"] >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
