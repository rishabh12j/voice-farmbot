# Comparison-system candidates (SEED LIST — nothing here is verified)

Status convention: every entry below is **CANDIDATE** — sourced from a web
search sweep (2026-07-04), descriptions and numbers come from abstracts or
search snippets and MUST NOT be cited until a dossier verifies them against
the actual paper. The Archaeologist may reject any candidate after reading it;
rejections are logged with a reason, not silently dropped.

The fronts mirror the paper's constraint table (social requirement → forced
constraint → framework delivery). A comparison set that only covers one front
defends only one row of the claim chain — every front needs at least one
dossier or an explicit [EVAL-GAP].

---

## Front 0 — the metrics source (mandatory first dossier)

| Candidate | Source | Why it matters | Verify first |
|---|---|---|---|
| Gugliermo et al., "Evaluating behavior trees" (Robotics and Autonomous Systems, 2024) | sciencedirect.com/science/article/pii/S0921889024000976 | Our DBSR/SNSR/USC attribution points here (evaluate_v2.py docstring, thesis interim). | Exact metric definitions vs our implementations; whether DBSR/SNSR/USC even appear under those names. |

## Front 1 — LLM×BT robot control (the architecture contrast: they GENERATE trees, we constrain the LLM to flat classification)

| Candidate | Source | Why it matters | Verify first |
|---|---|---|---|
| BTGenBot (arXiv 2403.12761) | lightweight-LLM BT generation | Same model class as ours (small LLMs) but opposite architecture — they fine-tune the LLM to EMIT trees. Sharpest possible contrast for framing (a). | Their success-rate definition, task set, robot platform. |
| BTGenBot-2 (arXiv 2602.01870) | SLM BT generation | Snippet claims SR 90.38% zero-shot / 98.07% one-shot — comparable-looking numbers IF definitions align. | Whether SR is executability or task completion; corpus size. |
| LLM-BRAIn (arXiv 2305.19352) | Alpaca-7B fine-tuned to generate BTs | Early canonical LLM→BT system; evaluated by human indistinguishability, not task success. | Their eval is subjective (4.53/10 discrimination) — likely NOT number-comparable; document as protocol contrast. |
| LLM-as-BT-Planner (arXiv 2409.10444) | BT generation, in-context + SFT | Sim + real evaluation of BT generation success. | Metrics, seeds, task suite. |
| BT generation w/ human feedback (arXiv 2409.09435) | sequential manipulation | Snippet claims 70.58% SR, failures from logical incoherence — exactly the failure mode our architecture excludes by construction. | SR definition; whether "executability" is separate. |

## Front 2 — safety / refusal under hazardous or wrong input (the USC=0 and refusal/negation front)

| Candidate | Source | Why it matters | Verify first |
|---|---|---|---|
| SafeAgentBench (arXiv 2412.13178) | 750-task embodied safety benchmark | Snippet: best baseline rejects only ~10% of hazardous detailed tasks — a headline contrast with our refusal/negation categories (100% clean refusal, USC 0) IF the settings are honestly bridged (their open task planning vs our fixed-verb interface = major deviation to ledger). | Rejection-rate definition; task type taxonomy; whether any baseline resembles a constrained-interface design. |
| KnowNo / "Robots That Ask For Help" (arXiv 2307.01928) | conformal-prediction help-asking | The principled version of our confirm-gate: calibrated uncertainty → ask a human. Compare their help-rate/success-guarantee protocol to our deterministic gate. | Their success-rate targets, calibration set, help-rate metric. |
| Pre-Execution Safety Gate & Task Safety Contracts (arXiv 2604.05427) | gate before execution for LLM-controlled robots | Architecturally the closest safety cousin (checks before acting). | What they measure for the gate (block rate? false-block rate?); overlap with our CheckBounds/CheckToolMounted prefix. |

## Front 3 — assistive voice robots + older-adult voice-AI evidence (the social front)

| Candidate | Source | Why it matters | Verify first |
|---|---|---|---|
| VoicePilot (arXiv 2404.04066) | LLM speech interface for physically assistive robots; study with 11 older adults (72–91) at an independent-living facility | The closest assistive-domain comparator: LLM + speech + physical robot + older adults. Their evaluation framework/instruments may anchor our (future-work) user-study design; their system-level metrics may be comparable now. | What they measure objectively (command success? latency?) vs subjectively; their safety handling. |
| "Situated Understanding of Errors in Older Adults' Interactions with Voice Assistants" (arXiv 2403.02421; ACM TACCESS) | month-long in-home study | The documented-harms anchor: error taxonomy in older-adult voice use — the empirical basis of the social thesis (candidate for the plan's [VA-Errors-24]-type keys). | Error taxonomy categories; which harms map to our by-construction preventions. |
| "It feels like hard work trying to talk to it" (arXiv 2510.06690) | 20-week deployment, 14 older adults, breakdown/repair | Second harms anchor: breakdown types + repair burden; trust erosion → abandonment. | Breakdown taxonomy; medication-management context transferability. |
| Voice-assistant trust after failures (mixed-methods; ResearchGate 370203002) | trust after VA failures | Trust-erosion mechanism our honest-or-blank rule targets. | Peer-reviewed venue?; failure-type taxonomy. |

## Front 4 — on-device / small-model robot control (the private-and-offline front)

| Candidate | Source | Why it matters | Verify first |
|---|---|---|---|
| Edge deployment of LLMs for mobile robots (arXiv 2405.17670) | LLMs controlling robots at the edge | Accuracy/latency trade-off protocol for edge LLM robot control — the axis our model-size sweep [EVAL-GAP] needs. | Models, quantisation, latency measurement points. |
| Small Models, Big Tasks (arXiv 2504.19277) | empirical SLM function-calling study | Function calling ≈ our flat intent classification; their accuracy protocol may anchor our corpus design. | Task taxonomy; model sizes; metric definitions. |
| SLM zero/one-shot leader-follower adaptation (arXiv 2602.23312) | SLMs in interaction | LLaMA-vs-Qwen efficiency/precision trade-offs at the edge. | Setting similarity. |

## Front 5 — voice control in agriculture (thin front — likely a novelty datum)

The sweep found NO peer-reviewed voice-controlled precision-agriculture robot
with a rigorous quantitative evaluation (closest: audio supervision of ag-robot
fleets, arXiv 2208.10455; assorted farming chatbots without robot actuation).
If dossier work confirms this absence, it becomes a stated novelty/positioning
point with the search documented — not a comparison row.

---

## Sweep provenance

Search sweep run 2026-07-04 (queries: Gugliermo BT metrics; LLM robot safety
benchmarks; LLM-BT generation; assistive voice robots older adults; on-device
SLM intent classification; agricultural voice robots). Snippets only — every
claim above requires source reading before use.
