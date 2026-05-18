"""V2 evaluation harness — measures the Gugliermo metrics against the
phone-to-Pi pipeline.

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
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: python3 -m pip install --user httpx")
    sys.exit(2)


# ---- test cases (29 utterances, lifted from growmate-bt/evaluate_bt.py) -----

TestCase = Tuple[str, str, List[str], str, str]
# (utterance, expected_type, expected_command_substrings, description, category)

TEST_CASES: List[TestCase] = [
    # Direct robot commands
    ("water the tomatoes",     "robot_command", ["M 400 200", "D_W_1"], "Direct water", "direct"),
    ("move to the herbs",      "robot_command", ["M 800 200"],          "Direct move",  "direct"),
    ("go home",                "robot_command", ["H_0"],                "Go home",      "direct"),
    ("turn on the lights",     "robot_command", ["D_L_1"],              "Light on",     "direct"),
    ("water all the plants",   "robot_command", ["P_4"],                "Water all",    "direct"),
    ("take a photo",           "robot_command", ["I_1"],                "Photo",        "direct"),
    ("check moisture levels",  "robot_command", ["P_9"],                "Moisture",     "direct"),
    ("move to the strawberries","robot_command",["M 800 1400"],         "Move berries", "direct"),
    ("scan for weeds",         "robot_command", ["I_4"],                "Weed scan",    "direct"),
    ("turn off the lights",    "robot_command", ["D_L_0"],              "Light off",    "direct"),

    # Indirect (LLM must infer intent)
    ("the herbs seem dry",         "robot_command", ["M 800 200"], "Indirect water",   "indirect"),
    ("the tomatoes look thirsty",  "robot_command", ["M 400 200"], "Indirect water 2", "indirect"),
    ("give the lettuce a drink",   "robot_command", ["M 1200 200"],"Informal water",   "indirect"),
    ("take care of the strawberries","robot_command",["M 800 1400"],"Vague command",   "indirect"),
    ("the carrots need attention", "robot_command", ["M 400 1400"],"Very indirect",    "indirect"),

    # Queries
    ("how are the tomatoes looking today","robot_query",["M 400 200"],"Status query","query"),
    ("is the soil moist enough",   "robot_query",   ["P_9"],         "Moisture query","query"),
    ("what's happening with the herbs","robot_query",["M 800 200"],  "Informal query","query"),
    ("check on the lettuce for me","robot_query",   ["M 1200 200"],  "Check query",  "query"),

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
    ("water the tomatoes and then go home","robot_command",["M 400 200","H_0"],"Multi","multi"),
    ("check on the herbs then water them", "robot_command",["M 800 200"],     "Multi q+c","multi"),

    # Safety edge
    ("water everything","robot_command",["P_4"],"Water all (needs confirm)","safety"),
]


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


# ---- intent generation ------------------------------------------------------


def _build_intent_payload(
    utterance: str,
    category: str,
    classifier,
) -> dict:
    """Use AICore (when available) to classify; otherwise hand-craft.

    The harness gracefully degrades when Ollama isn't running so we can
    still smoke-test the Pi side without a live LLM.
    """
    if classifier is not None and classifier.is_available():
        intents = classifier._classify(utterance) or []
        if intents:
            return {
                "intents": intents,
                "raw_text": utterance,
                "emergency": category == "emergency",
                "client_id": "evaluate_v2",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "schema_version": "1.0.0",
            }

    # Fallback: emergency utterances stop the BT; everything else gets a
    # single placeholder intent so the Pi still does something measurable.
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


def _try_import_ai_core():
    try:
        from growmate_voice.ai_core import AICore  # type: ignore[import-not-found]
        cfg = REPO_ROOT / "src" / "growmate_voice" / "config" / "farmbot.yaml"
        return AICore(config_path=str(cfg))
    except Exception as exc:
        print(f"[eval] AICore unavailable ({exc}); using fallback intents")
        return None


# ---- main eval loop ---------------------------------------------------------


def run_eval(pi_url: str, use_llm: bool, http_timeout_s: float) -> List[CaseResult]:
    classifier = _try_import_ai_core() if use_llm else None
    results: List[CaseResult] = []

    with httpx.Client(timeout=http_timeout_s) as client:
        for utterance, _exp_type, exp_cmds, desc, category in TEST_CASES:
            payload = _build_intent_payload(utterance, category, classifier)
            t0 = time.monotonic()
            try:
                r = client.post(pi_url, json=payload)
                r.raise_for_status()
                reply = r.json()
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

            dbsr_pass = all(any(c in cmd for cmd in commands) for c in exp_cmds) \
                if exp_cmds else reply.get("status") in ("success", "partial")

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
    return {
        "n_cases": total,
        "DBSR": round(dbsr_pass / max(total, 1) * 100, 1),
        "SNSR": round(total_succ / max(total_nodes, 1) * 100, 1),
        "USC": usc,
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
    args = ap.parse_args(argv)

    print(f"# V2 eval target: {args.pi_url}")
    results = run_eval(args.pi_url, use_llm=not args.no_llm, http_timeout_s=args.timeout)
    summary = _summarise(results)

    if args.json:
        print(json.dumps({"summary": summary, "cases": [r.__dict__ for r in results]}, indent=2))
        return 0

    print(f"\n{'category':<10} {'utterance':<42} {'status':<8} {'dbsr':<5} {'ms':<6}")
    print("-" * 75)
    for r in results:
        flag = "OK" if r.dbsr_pass else "MISS"
        print(f"{r.category:<10} {r.utterance[:40]:<42} {r.pi_status:<8} {flag:<5} {r.duration_ms:<6}")

    print(f"\nSummary: {summary}")
    return 0 if summary["DBSR"] >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
