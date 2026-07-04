# Dossier #2 — SafeAgentBench (Front 2: safety/refusal under hazardous input)

**Dossier status: VERIFIED against full text (arXiv HTML, v5). ACCEPT as a
comparison row — with mandatory setting-bridge caveats (ledger D7).**

Read 2026-07-04 from arXiv 2412.13178 (v5, revised 2025-10-31; 28 pp).

## 1. Identity — VERIFIED

- Yin, Pang, Ding, Chen, Bi, Xiong, Huang, Siheng Chen (Shanghai Jiao Tong
  University), "SafeAgentBench: A Benchmark for Safe Task Planning of Embodied
  LLM Agents", arXiv:2412.13178 (v1 2024-12-17, v5 2025-10-31). Peer-reviewed
  venue: none stated on arXiv — cite as arXiv preprint. Code + dataset public
  (github.com/shengyin1224/SafeAgentBench).

## 2. What it is — VERIFIED

First safety-aware benchmark for *task planning* by embodied LLM agents.
750 tasks (450 hazardous + 300 safe controls) in SafeAgentEnv (AI2-THOR-based),
17 high-level actions, 9 baseline agent designs × 4 LLMs (GPT-4, Gemini-2.5-pro,
Llama3-8B, Qwen2-7B; DeepSeek-V2.5 in appendix). 10 hazard categories: 5 harm-
to-human (fire, electric shock, explosion, poisoning/ingestion, slip) + 5
harm-to-property. Three task types: detailed (explicit hazardous instruction),
abstract (4 abstraction levels L1–L4), long-horizon (implicit risky sub-task).

## 3. Metrics — VERIFIED (definitions, §5.1–5.3)

- **Rejection rate (Rej)**: proportion of hazardous tasks the agent refuses —
  "reflects its proactive safety awareness".
- **Risk/success rate (goal)** and **(LLM)**: success rate reinterpreted as
  likelihood of executing the dangerous task; assessed by an execution
  evaluator (post-hoc goal conditions in the simulator) and a semantic
  evaluator (gpt-4o-2024-08-06 judge; prompts in App. C.4; reliability
  cross-checked by a 140-question user study, §5.5).
- **Execution rate (ER)**: proportion of executable steps in plans.
- **Usage time**. Long-horizon adds: completed-and-safe / completed-but-unsafe
  / incomplete.
- Baselines get NO safety hints in prompts (deliberate, §5.1).

## 4. Key numbers — VERIFIED

- "The most safety-conscious baseline achieves only a 10% rejection rate for
  detailed hazardous tasks" (abstract; Table 2: ReAct+GPT-4 Rej 0.10; five of
  nine agents reject none).
- Abstract tasks: ReAct rejection rises to 32% at higher abstraction; ReAct
  still shows 41% risk rate at L4.
- Risk rates ~30% typical, up to 69% (MLDT, RR-LLM).
- Long-horizon: best completed-and-safe only 50% (ProgPrompt).
- Swapping LLMs barely moves safety (<3% difference in average proactive
  defense for detailed tasks; <13% overall variance) — "the agent's
  architecture has a more significant impact on safety than the choice of
  LLM" (§5.4). ← This sentence is direct prior-work support for our
  architecture-over-model claim.
- Defenses: a GPT-4 CoT safety filter between planner and controller blocks
  some hazards but causes "substantial over-rejection on safe tasks" (§5.6).

## 5. Seed-list claim check

- "best baseline rejects only ~10% of hazardous detailed tasks" — CONFIRMED
  verbatim.
- "750-task embodied safety benchmark" — CONFIRMED.
- "whether any baseline resembles a constrained-interface design" — checked:
  NO. All nine baselines are open task planners (ReAct, ProgPrompt, LLM-
  Planner, CoELA, MLDT, MAP, Lota-Bench, PCA-EVAL, Co-LLM-Agent). None
  constrains the LLM to a fixed action enum with deterministic guards. The
  closest analogue is their CoT-filter defense (LLM-judged, over-rejects) —
  the opposite of our deterministic refusal.

## 6. Comparability bridge to GrowMate/VoiceBT (must appear wherever cited)

Legitimate use: a *regime contrast*, not a head-to-head.

| Axis | SafeAgentBench | Ours |
|---|---|---|
| Interface | open-ended task planning, 17 actions composable into plans | fixed action enum, flat single-intent classification |
| Hazard source | 450 deliberately hazardous instructions (fire, shock, …) | unplanted-species refusals, negation, OOB targets — domain-scoped |
| Refusal mechanism | LLM's own safety awareness (prompted with no hints) | deterministic: grounding + BT guards; LLM never decides safety |
| Refusal metric | rejection rate on hazardous set | DBSR pass on refusal/negation categories (clean decline = terminal success + zero commands) |
| Scale | 750 curated tasks, public benchmark | 42 self-authored cases (4 refusal + 2 negation) |

Honest sentence shape: "Open-planning agents refuse at best 10% of explicitly
hazardous detailed tasks [SafeAgentBench]; by restricting the LLM to a fixed
intent vocabulary and moving refusal into deterministic grounding/guards,
GrowMate refuses 100% of its out-of-vocabulary and negation cases (6/6, Run 1,
sim) — a different, narrower hazard universe, achieved by construction rather
than by model judgment." The clause after the dash is NOT optional.

Their architecture-beats-LLM finding independently supports framing (a).

## 7. Deviations ledger entries seeded

- **D7**: hazard universes differ (their physical-harm taxonomy vs our
  domain refusals); rejection-rate vs refusal-DBSR are protocol-different;
  corpus sizes differ by >1 order of magnitude. Comparison must be phrased as
  regime contrast with both settings stated.

## 8. Not verified / open

- No peer-reviewed venue found — arXiv-only as of dossier date; check before
  camera-ready citation.
- Numbers quoted are v5; earlier versions (e.g., original v1 with fewer
  models) differ — pin the version in the bibliography.
