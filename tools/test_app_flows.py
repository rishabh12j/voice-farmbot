"""End-to-end flow tests for the GrowMate voice app — no hardware, no browser.

Drives the FastAPI app **in-process** (fastapi TestClient) exactly the way the
browser does — /api/text, /api/action, /api/confirm, /api/estop, /api/reset —
against a **sim intent server** (growmate_pi.intent_server --no-ros2), so every
app flow is exercised end-to-end: routing, confirm gate, Pi dispatch, honest
error paths, task lifecycle, and the history/pipeline-log sequence.

Two check classes:
  FLOW — deterministic app behaviour (routing, gates, honesty, sequencing).
         A FLOW failure is an app bug.
  NLP  — depends on live LLM classification (Ollama gemma3:4b). A NLP failure
         is a classification miss, not necessarily an app bug. Reported
         separately so the two don't get conflated.

Run:
    # terminal 1 — sim Pi
    PYTHONPATH=src python -m growmate_pi.intent_server --no-ros2 --port 8123
    # terminal 2 — the suite (repo root)
    PYTHONPATH=src python tools/test_app_flows.py [--sim-url http://localhost:8123]

Expect: "FLOW failures: 0". NLP misses are listed for prompt tuning.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Windows consoles default to cp1252, which can't print the app's emoji log
# markers — reconfigure stdout so the suite never dies on a print().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
# growmate_pi lives at src/, the app package at src/growmate_voice/ (ROS layout).
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "growmate_voice"))

# TTS/STT stay untouched: every request sends enable_tts=false.
from fastapi.testclient import TestClient  # noqa: E402

import growmate_voice.app as appmod  # noqa: E402


# --------------------------------------------------------------------------- helpers
class Check:
    """One assertion with evidence. kind: FLOW | NLP."""

    def __init__(self, kind: str, label: str, ok: bool, evidence: str = ""):
        self.kind, self.label, self.ok, self.evidence = kind, label, bool(ok), evidence


class Suite:
    def __init__(self, client: TestClient, sim_url: str):
        self.client = client
        self.sim = sim_url.rstrip("/")
        self.checks: List[Check] = []

    # -- plumbing ---------------------------------------------------------
    def check(self, kind: str, label: str, ok: bool, evidence: str = "") -> bool:
        self.checks.append(Check(kind, label, ok, evidence))
        mark = "PASS" if ok else "FAIL"
        print(f"    [{kind}] {mark}  {label}" + ("" if ok else f"   <- {evidence}"))
        return bool(ok)

    def text(self, phrase: str) -> Dict[str, Any]:
        r = self.client.post("/api/text", json={
            "text": phrase, "tts": "none", "enable_tts": "false"})
        body = r.json()
        body["_status_code"] = r.status_code
        return body

    def action(self, action: str) -> Dict[str, Any]:
        r = self.client.post("/api/action", json={"action": action})
        return r.json()

    def confirm(self, cid: str, yes: bool) -> Dict[str, Any]:
        r = self.client.post("/api/confirm", json={
            "confirm_id": cid, "confirmed": yes, "tts": "none",
            "enable_tts": "false"})
        return r.json()

    def history(self) -> List[Dict[str, Any]]:
        """History rows in CHRONOLOGICAL order (the API returns newest-first)."""
        rows = self.client.get("/api/history?limit=100").json().get("entries", [])
        return list(reversed(rows))

    def sim_status(self) -> Dict[str, Any]:
        with httpx.Client(timeout=5.0) as c:
            return c.get(self.sim + "/status").json()

    def sim_task(self) -> Dict[str, Any]:
        return self.sim_status().get("task") or {}

    def wait_task(self, predicate, timeout_s: float = 60.0,
                  poll_s: float = 0.5) -> Optional[Dict[str, Any]]:
        """Poll the sim task until predicate(task) is truthy; None on timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            task = self.sim_task()
            if predicate(task):
                return task
            time.sleep(poll_s)
        return None

    def last_event_id(self) -> int:
        try:
            with httpx.Client(timeout=5.0) as c:
                rows = c.get(self.sim + "/events?limit=1").json().get("events") or []
            return rows[0].get("id", 0) if rows else 0
        except Exception:
            return 0

    def wait_watered_event(self, species: str, after_id: int,
                           timeout_s: float = 90.0) -> Optional[Dict[str, Any]]:
        """Wait for a NEW 'watered' event-log row naming ``species``.

        Grace-window-proof completion detection: a tiny bed (1 plant) can
        finish entirely inside the Pi's /intent inline window, so task_active
        is never observable — but the honest log row always lands.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=5.0) as c:
                    rows = c.get(self.sim + "/events?limit=10").json().get("events") or []
                for r in rows:
                    if (r.get("id", 0) > after_id
                            and r.get("event_type") == "watered"
                            and species in (r.get("plant_name") or "").lower()):
                        return r
            except Exception:
                pass
            time.sleep(1.0)
        return None

    def estop_and_reset(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Halt whatever is running, then re-arm. Returns (estop, reset) payloads."""
        e = self.client.post("/api/estop").json()
        # Give the in-flight tree a tick to notice and unwind.
        self.wait_task(lambda t: not t.get("task_active"), timeout_s=15)
        r = self.client.post("/api/reset").json()
        time.sleep(0.5)
        return e, r

    @staticmethod
    def log_lines(body: Dict[str, Any]) -> List[str]:
        return [ln.strip() for ln in (body.get("log") or "").splitlines() if ln.strip()]

    def audit_pipeline_log(self, body: Dict[str, Any], scenario: str) -> None:
        """Every /api/text response's log must be: input -> route -> dispatch -> Done."""
        lines = self.log_lines(body)
        ok_start = bool(lines) and lines[0].startswith("📝")
        has_route = any(l.startswith("🔍") for l in lines)
        ok_end = bool(lines) and lines[-1].startswith("✅")
        self.check("FLOW", f"{scenario}: pipeline log ordered (📝→🔍→…→✅)",
                   ok_start and has_route and ok_end, " | ".join(lines))

    def last_history(self, n: int = 1) -> List[Dict[str, Any]]:
        return self.history()[-n:]


# --------------------------------------------------------------------------- scenarios
def scenario_ui_script_parses(s: Suite) -> None:
    print("\n=== 0. UI page serves and its embedded JS parses ===")
    r = s.client.get("/")
    s.check("FLOW", "GET / serves the app page", r.status_code == 200,
            f"status={r.status_code}")
    # A single JS SyntaxError in the embedded script kills EVERY browser
    # handler silently (the duplicate-const todayCard bug bricked the power
    # button, voice and text at once). Parse each <script> with node so that
    # whole failure class is caught here, not on stage.
    import re as _re
    import subprocess as _sp
    import tempfile as _tf
    scripts = _re.findall(r"<script[^>]*>(.*?)</script>", r.text,
                          flags=_re.DOTALL | _re.IGNORECASE)
    scripts = [t for t in scripts if t.strip()]
    try:
        _sp.run(["node", "--version"], capture_output=True, timeout=10, check=True)
    except Exception:
        print("    (node unavailable — JS parse check skipped)")
        return
    bad = []
    for i, body in enumerate(scripts):
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False,
                                    encoding="utf-8") as f:
            f.write(body)
            path = f.name
        try:
            p = _sp.run(["node", "--check", path], capture_output=True,
                        text=True, timeout=30)
            if p.returncode != 0:
                err = (p.stderr or "").strip().splitlines()
                bad.append(f"script #{i}: {err[-1] if err else 'parse error'}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    s.check("FLOW", f"all {len(scripts)} embedded <script> blocks parse (node --check)",
            not bad, " ; ".join(bad))


def scenario_estop_reset(s: Suite) -> None:
    print("\n=== 1. Safety: estop + reset roundtrip ===")
    e, r = s.estop_and_reset()
    s.check("FLOW", "estop speaks 'Stopped'", "stopped" in (e.get("tts_text") or "").lower(),
            str(e.get("tts_text")))
    s.check("FLOW", "reset speaks 'All clear'", "all clear" in (r.get("tts_text") or "").lower(),
            str(r.get("tts_text")))
    rows = s.last_history(2)
    kinds = [row.get("action") for row in rows]
    s.check("FLOW", "history logs estop then reset in order",
            kinds == ["estop", "reset"], str(kinds))


def scenario_pattern_quick(s: Suite) -> None:
    print("\n=== 2. Pattern route: quick commands ===")
    for phrase, want_action, want_tts in [
        ("go home", "home", "heading home"),
        ("turn on the lights", "light_on", "lights on"),
        ("turn off the lights", "light_off", "lights off"),
    ]:
        body = s.text(phrase)
        res = body.get("result") or {}
        pos = res.get("position") or {}
        s.check("FLOW", f"'{phrase}' pattern-matched to {want_action}",
                res.get("matched_action") == want_action,
                f"matched={res.get('matched_action')} route log={s.log_lines(body)}")
        s.check("FLOW", f"'{phrase}' spoken text is forward-tense status",
                want_tts in (pos.get("tts_text") or "").lower(),
                str(pos.get("tts_text")))
        s.check("FLOW", f"'{phrase}' not suppressed",
                not pos.get("suppress_voice"), str(pos.get("suppress_voice")))
        s.audit_pipeline_log(body, phrase)
        row = s.last_history(1)[0]
        s.check("FLOW", f"'{phrase}' history row transcript recorded",
                row.get("transcript") == phrase, str(row))
    # go_home publishes to the sim; give it a beat then make sure nothing hung.
    s.wait_task(lambda t: not t.get("task_active"), timeout_s=20)


def scenario_water_known(s: Suite) -> None:
    print("\n=== 3. AICore route: water a planted species (async task + estop cut) ===")
    body = s.text("water the tomatoes")
    res = body.get("result") or {}
    pos = res.get("position") or {}
    s.audit_pipeline_log(body, "water tomatoes")
    # The sim map has 11 tomatoes -> the Tier-B multi-plant gate (N >= 5) must
    # defer for confirmation before a long watering run. Answer YES.
    if res.get("requires_confirm"):
        s.check("FLOW", "multi-plant water deferred for confirmation (N>=5)",
                True, res.get("confirm_question") or "")
        yes = s.confirm(res.get("confirm_id") or "", yes=True)
        pos = ((yes.get("result") or {}).get("position")) or pos
    else:
        s.check("FLOW", "multi-plant water deferred for confirmation (N>=5)",
                False, f"no confirm for 11 tomatoes: {str(res)[:200]}")
    # The Pi accepted it as a background task:
    task = s.wait_task(lambda t: t.get("task_active"), timeout_s=30)
    ok_task = task is not None
    s.check("NLP", "sim Pi started a watering task", ok_task, str(task))
    if ok_task:
        s.check("FLOW", "task label is honest forward-tense ('Watering N …')",
                (task.get("task_label") or "").lower().startswith("watering"),
                str(task.get("task_label")))
        s.check("FLOW", "voice suppressed while task runs (single narrator)",
                bool(pos.get("suppress_voice")), str(pos))
        # Let at least one plant complete so the honest log gains a row.
        s.wait_task(lambda t: (t.get("current_step") or 0) >= 2, timeout_s=90)
    e, r = s.estop_and_reset()
    s.check("FLOW", "estop halts the running water (task_active false)",
            not s.sim_task().get("task_active"), str(s.sim_task()))


def scenario_water_unknown(s: Suite) -> None:
    print("\n=== 4. AICore route: water an UNPLANTED species -> clean refusal ===")
    before = s.sim_task().get("revision")
    body = s.text("water the bananas")
    res = body.get("result") or {}
    pos = res.get("position") or {}
    tts = (pos.get("tts_text") or "").lower()
    s.check("NLP", "refusal mentions it can't find bananas",
            ("don't see" in tts) or ("no banana" in tts) or ("don't have" in tts), tts)
    s.check("FLOW", "refusal is spoken (not suppressed)",
            not pos.get("suppress_voice"), str(pos.get("suppress_voice")))
    time.sleep(2)
    s.check("FLOW", "no robot task started for unknown species (USC)",
            not s.sim_task().get("task_active"),
            str(s.sim_task()))


def scenario_confirm_gate(s: Suite) -> None:
    print("\n=== 5. Confirm gate: 'water everything' defers, NO cancels ===")
    body = s.text("water everything")
    res = body.get("result") or {}
    s.check("FLOW", "'water everything' requires confirmation",
            bool(res.get("requires_confirm")), str(res)[:200])
    cid = res.get("confirm_id") or ""
    q = (res.get("confirm_question") or "")
    s.check("FLOW", "confirm question restates what was heard",
            "water everything" in q.lower(), q)
    no = s.confirm(cid, yes=False)
    s.check("FLOW", "NO cancels with spoken 'Cancelled'",
            bool(no.get("cancelled")) and "cancel" in
            ((no.get("result") or {}).get("tts_spoken") or "").lower(), str(no)[:200])
    time.sleep(2)
    s.check("FLOW", "no task started after cancel",
            not s.sim_task().get("task_active"), str(s.sim_task()))

    print("\n=== 6. Confirm gate: YES dispatches (then estop cut) ===")
    body = s.text("water everything")
    cid = ((body.get("result") or {}).get("confirm_id")) or ""
    s.check("FLOW", "second ask defers again with a fresh id", bool(cid), str(cid))
    yes = s.confirm(cid, yes=True)
    task = s.wait_task(lambda t: t.get("task_active"), timeout_s=45)
    s.check("NLP", "YES starts the water-all task", task is not None,
            f"confirm reply={str(yes)[:150]} task={s.sim_task()}")
    s.estop_and_reset()

    print("\n=== 7. Confirm gate: expired/unknown id is honest ===")
    stale = s.confirm("deadbeef", yes=True)
    s.check("FLOW", "unknown confirm id reports expiry, no dispatch",
            bool(stale.get("expired")), str(stale)[:200])


def scenario_pi_down_honesty(s: Suite) -> None:
    print("\n=== 8. Honest-or-blank: Pi unreachable mid-demo ===")
    real = appmod._STATE.pi_url
    appmod._STATE.pi_url = "http://localhost:9/intent"   # dead port
    try:
        # Voice/text path:
        body = s.text("water the tomatoes")
        pos = (body.get("result") or {}).get("position") or {}
        tts = (pos.get("tts_text") or "").lower()
        s.check("FLOW", "text path speaks lost-connection (no silent fake)",
                "lost the connection" in tts, tts or "(empty)")
        row = s.last_history(1)[0]
        s.check("FLOW", "text path history row recorded as error",
                row.get("status") == "error", str(row))
        # Button path:
        act = s.action("water")
        tts2 = (act.get("tts_text") or "").lower()
        s.check("FLOW", "button path speaks lost-connection",
                "lost the connection" in tts2, tts2 or "(empty)")
        row2 = s.last_history(1)[0]
        s.check("FLOW", "button path did NOT emit local P_4 (no fake 'sent')",
                row2.get("status") == "error" and "P_4" not in str(row2.get("emitted")),
                str(row2))
    finally:
        appmod._STATE.pi_url = real


def scenario_fast_path(s: Suite) -> None:
    print("\n=== 9. Fast-path plant query (deterministic, no LLM) ===")
    appmod._STATE.pi_verify_enabled = True   # gate: honest log is live
    body = s.text("when did I last water the tomatoes")
    lines = s.log_lines(body)
    fast = any(l.startswith("⚡") for l in lines)
    s.check("FLOW", "plant-state query answered by fast path (no LLM)",
            fast, " | ".join(lines))
    pos = (body.get("result") or {}).get("position") or {}
    s.check("FLOW", "fast path returned a spoken answer",
            bool((pos.get("tts_text") or "").strip()), str(pos.get("tts_text")))


def scenario_gibberish(s: Suite) -> None:
    print("\n=== 10. Gibberish -> no motion (app-level USC) ===")
    body = s.text("purple elephant dancing on the moon")
    time.sleep(2)
    s.check("FLOW", "no robot task started for gibberish",
            not s.sim_task().get("task_active"), str(s.sim_task()))
    pos = (body.get("result") or {}).get("position") or {}
    s.check("FLOW", "app said something (never silent)",
            bool((pos.get("tts_text") or pos.get("last_cmd") or "").strip()),
            str(pos)[:150])


def scenario_pronoun_memory(s: Suite) -> None:
    print("\n=== 12. Session memory: 'water them again' resolves the pronoun ===")
    # Establish the memory: water a SMALL bed (spearmint — below the
    # multi-plant confirm threshold). Completion is detected via the honest
    # event log, NOT task_active: a 1-plant water can finish entirely inside
    # the Pi's /intent inline grace window, so the task is never observable.
    mark = s.last_event_id()
    body = s.text("water the spearmint")
    res = body.get("result") or {}
    if res.get("requires_confirm"):   # threshold could change — just answer
        s.confirm(res.get("confirm_id") or "", yes=True)
    row = s.wait_watered_event("spearmint", mark)
    s.check("NLP", "setup: spearmint watered (honest log row)", row is not None,
            f"no new watered event after id {mark}")
    s.wait_task(lambda t: not t.get("task_active"), timeout_s=120)

    # Now the follow-up with no plant named at all:
    mark = s.last_event_id()
    body = s.text("water them again")
    res = body.get("result") or {}
    if res.get("requires_confirm"):
        s.confirm(res.get("confirm_id") or "", yes=True)
    row = s.wait_watered_event("spearmint", mark)
    s.check("NLP", "'water them again' resolved to the spearmint (memory)",
            row is not None, f"no new spearmint watered event after id {mark}")
    s.wait_task(lambda t: not t.get("task_active"), timeout_s=120)


def scenario_multi_intent(s: Suite) -> None:
    print("\n=== 11. Multi-intent: 'water the tomatoes and then go home' ===")
    body = s.text("water the tomatoes and then go home")
    s.audit_pipeline_log(body, "multi-intent")
    res = body.get("result") or {}
    # 11 tomatoes -> multi-plant gate defers here too; answer YES to proceed.
    if res.get("requires_confirm"):
        s.confirm(res.get("confirm_id") or "", yes=True)
    task = s.wait_task(lambda t: t.get("task_active"), timeout_s=30)
    s.check("NLP", "compound command starts a task", task is not None, str(task))
    s.estop_and_reset()


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GrowMate app flow tests (sim, no hardware)")
    ap.add_argument("--sim-url", default=os.environ.get("SIM_PI_URL", "http://localhost:8123"),
                    help="sim intent server base URL (default %(default)s)")
    ap.add_argument("--skip-llm", action="store_true",
                    help="skip scenarios that need Ollama (NLP checks)")
    args = ap.parse_args(argv)

    # Sim server must be up first.
    try:
        with httpx.Client(timeout=4.0) as c:
            st = c.get(args.sim_url.rstrip("/") + "/status").json()
        assert st.get("ok")
    except Exception as exc:
        print(f"FATAL: sim intent server not reachable at {args.sim_url}: {exc}")
        print("Start it with: PYTHONPATH=src python -m growmate_pi.intent_server --no-ros2 --port 8123")
        return 2
    print(f"Sim Pi at {args.sim_url}  (bridge_mode={st.get('bridge_mode')}, "
          f"verify_enabled={st.get('verify_enabled')})")

    # App in-process, wired to the sim exactly like `--no-ros2 --pi-url …`.
    appmod._ensure_initialised(ros2_enabled=False)
    appmod._STATE.pi_url = args.sim_url.rstrip("/") + "/intent"
    appmod._STATE.pi_verify_enabled = bool(st.get("verify_enabled"))
    client = TestClient(appmod.app)
    s = Suite(client, args.sim_url)

    # Test isolation: the app persists history across sessions
    # (~/.growmate_voice/history.jsonl) — start each run from a clean slate.
    client.post("/api/history/clear")

    llm_ok = False
    if not args.skip_llm:
        ai = appmod._get_aicore()
        llm_ok = ai is not None
        print(f"AICore/Ollama: {'ready' if llm_ok else 'NOT available — LLM scenarios skipped'}")

    scenarios = [
        (scenario_ui_script_parses, False),
        (scenario_estop_reset, False),
        (scenario_pattern_quick, False),
        (scenario_water_known, True),
        (scenario_water_unknown, True),
        (scenario_confirm_gate, True),
        (scenario_pi_down_honesty, True),
        (scenario_fast_path, False),
        (scenario_gibberish, True),
        (scenario_multi_intent, True),
        (scenario_pronoun_memory, True),
    ]
    for fn, needs_llm in scenarios:
        if needs_llm and not llm_ok:
            print(f"\n(skipped {fn.__name__} — needs LLM)")
            continue
        try:
            fn(s)
        except Exception:
            s.check("FLOW", f"{fn.__name__} completed without crashing", False,
                    traceback.format_exc(limit=3).replace("\n", " | "))
            # Leave the sim in a sane state for whatever runs next.
            try:
                s.estop_and_reset()
            except Exception:
                pass

    # ------------------------------------------------------------------ report
    flow = [c for c in s.checks if c.kind == "FLOW"]
    nlp = [c for c in s.checks if c.kind == "NLP"]
    flow_fail = [c for c in flow if not c.ok]
    nlp_fail = [c for c in nlp if not c.ok]
    print("\n" + "=" * 70)
    print(f"FLOW checks : {len(flow) - len(flow_fail)}/{len(flow)} passed")
    print(f"NLP  checks : {len(nlp) - len(nlp_fail)}/{len(nlp)} passed")
    if flow_fail:
        print("\nFLOW failures (app bugs):")
        for c in flow_fail:
            print(f"  - {c.label}\n      {c.evidence[:300]}")
    if nlp_fail:
        print("\nNLP misses (classification tuning targets):")
        for c in nlp_fail:
            print(f"  - {c.label}\n      {c.evidence[:300]}")
    print(f"\nFLOW failures: {len(flow_fail)}   NLP misses: {len(nlp_fail)}")
    return 1 if flow_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
