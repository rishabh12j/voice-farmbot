# GrowMate V2 — Architecture Plan

## What we want to change

Current flow — everything runs on the Windows machine:
```
Browser mic → WAV → FastAPI (Windows)
  → STT (Whisper)
  → LLM (Ollama, gemma3:4b) → intent JSON
  → AICore._intent_to_tree() → BT dict (pure Python)
  → BTEngine.execute() → command strings
  → ROS2Publisher → keyboard_topic → FarmBot Pi
```

Target flow — split client vs Pi:
```
Phone/Browser
  → mic → STT
  → LLM call (Ollama on Windows, over LAN) → intent JSON
  → HTTP POST intent JSON to Pi

FarmBot Pi
  → receives intent JSON
  → py_trees BT construction (from EE650 bt_nodes pattern)
  → BT tick/execute
  → keyboard_topic → FarmBot hardware
```

---

## Key decisions before writing a line of code

| # | Decision | Options | Implication |
|---|----------|---------|-------------|
| 1 | Where does STT run? | A) Windows machine (current) · B) Pi · C) Phone-side WASM | A is easiest — phone sends WAV to Windows, Windows transcribes |
| 2 | Where does LLM run? | A) Windows (current Ollama) · B) Cloud API · C) Pi (too slow for 4B) | A — Pi stays LLM-free, Windows Ollama stays as-is |
| 3 | What does the Pi receive? | A) Raw text · B) Intent JSON · C) Full BT JSON | B — intent JSON: `{"action": "water", "target": "tomatoes"}` |
| 4 | BT library on Pi | A) py_trees (EE650 pattern) · B) Keep dict-based BTEngine · C) PlanSys2+PDDL | Discussed below |
| 5 | HTTPS for phone mic | Needed for Safari/Chrome mic on LAN | ngrok or caddy, unrelated to split |

---

## BT approach on Pi — three routes

### Route A: py_trees (from EE650)
- Real BT ticks, reactive, handles RUNNING state naturally
- We already have the node classes written (bt_nodes.py from EE650)
- No planner needed — intent JSON maps directly to a subtree
- Works on Pi without PlanSys2 installed
- **Best fit for single-intent commands (water, move, photo)**

### Route B: PlanSys2 + PDDL (from EE650 mission_controller)
- Good when there are multi-step goals: "visit all plants that need water, in priority order"
- Requires PlanSys2 and POPF installed on Pi (heavyweight)
- Overkill for single voice commands — adds latency for no gain
- **Only worth it if we add multi-step planning as a feature**

### Route C: Keep current dict-based BTEngine, move it to Pi
- Least disruption — just move execution to Pi
- No py_trees, no PDDL
- Misses the reactive/ticking benefits of a real BT library
- **Easy migration path but no architectural improvement**

**Recommendation: Route A (py_trees) for now, with Route B as optional future layer for multi-step planning.**

---

## Components: reuse vs build new

### Reuse as-is (Windows side)
| Component | File | Notes |
|-----------|------|-------|
| STT pipeline | `edgespeech/stt/` | No change |
| LLM intent classifier | `ai_core.py` | No change — still returns intent JSON |
| FastAPI voice endpoint | `app.py` | Strip out BT execution; POST intent to Pi instead |
| Emergency stop | `app.py /estop` | Still direct — bypasses Pi, publishes `e` directly |

### Build new (Pi side)
| Component | What | Based on |
|-----------|------|----------|
| Intent receiver | FastAPI endpoint on Pi: `POST /intent` | New — thin FastAPI on Pi |
| py_trees node library | Condition + action nodes for FarmBot commands | Port from EE650 `bt_nodes.py` |
| BT builder | Maps intent JSON → py_trees tree | Replaces `AICore._intent_to_tree()` |
| BT executor | Ticks the tree to completion | Replaces `BTEngine.execute()` |
| Safety nodes | `IsAvailable`, `InBounds`, `PlantFound` as py_trees conditions | Port from current `bt_engine.py` logic |

### Retire / simplify
| Component | What happens |
|-----------|-------------|
| `bt_engine.py` | Replaced by py_trees executor on Pi |
| `ros2_publisher.py` | Stays on Pi only — removed from Windows app |
| BT dict format | Replaced by py_trees composites |

---

## What the intent JSON looks like (contract between client and Pi)

```json
{
  "action": "water",
  "target": "tomatoes",
  "params": {
    "x": 400, "y": 200, "z": -100,
    "duration": 6
  },
  "emergency": false,
  "raw_text": "water the tomatoes please"
}
```

Pi takes this, constructs the py_trees subtree, ticks it. Simple.

---

## Build order (sequential — each step depends on previous)

| Step | What | Where | Status |
|------|------|-------|--------|
| 1 | Define intent JSON schema (contract) | Design doc | `[ ]` |
| 2 | Port EE650 bt_nodes to FarmBot actions (water, move, estop, photo, LED, sensor) | Pi package | `[ ]` |
| 3 | Write BT builder: intent JSON → py_trees tree | Pi package | `[ ]` |
| 4 | Write Pi FastAPI: `POST /intent` → tick tree → return result | Pi package | `[ ]` |
| 5 | Modify Windows `app.py`: after LLM classify, POST to Pi instead of running BTEngine | Windows app | `[ ]` |
| 6 | Test end-to-end in sim (Pi in --no-ros2 mode, Windows app → POST → Pi) | Both | `[ ]` |
| 7 | Test on real hardware (Pi with ROS2, actual FarmBot) | Hardware | `[ ]` |

---

## Confirmed decisions

| Question | Answer |
|----------|--------|
| Who calls the Pi? | Phone calls Pi directly — no Windows in the request path |
| py_trees on Pi? | Not installed — needs `pip install py_trees` (not in apt on Humble) |
| PlanSys2 / PDDL? | Keep — useful for multi-step planning layer later |

## Remaining open question (critical — changes build order)

**Where does the LLM run?**

Pi cannot run gemma3:4b fast enough for interactive use. Three options:

| Option | Flow | Trade-off |
|--------|------|-----------|
| A | Phone → STT on-device → calls Ollama on Windows (LAN) → intent JSON → POST to Pi | Windows as Ollama-only server; phone needs network reach to Windows |
| B | Phone → WAV → Pi → Pi does STT + calls Ollama on Windows → intent JSON → BT | Pi is the single server; Windows is a dumb Ollama host |
| C | Pi runs a tiny model (gemma3n:e2b ~2B) locally | Eliminates Windows entirely; latency on Pi hardware unknown |

**Recommended: Option B** — Pi is the single endpoint the phone talks to. Pi handles STT, calls Windows Ollama over LAN for classification, builds py_trees BT, executes. Windows eventually replaceable by cloud endpoint or Option C once we benchmark Pi latency.
