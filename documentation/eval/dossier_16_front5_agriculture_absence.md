# Dossier #16 — Front 5: voice control in agriculture (documented absence)

**Dossier status: absence CONFIRMED as far as one sweep + dossier checks can —
framed as a positioning point with the search documented, per the seed list's
own recommendation. The closest system is verified below.**

Work date 2026-07-04.

## 1. The closest existing work — VERIFIED

- Kamboj, Ji, Driggs-Campbell (UIUC), "Examining Audio Communication
  Mechanisms for Supervising Fleets of Agricultural Robots", **IEEE RO-MAN
  2022** (arXiv comments: camera-ready for RO-MAN 2022), arXiv:2208.10455.
- What it actually is: a **simulation** user study of *audio notification*
  mechanisms (earcons vs single-phrase vs full sentences) for a remote
  operator supervising five simulated agbots that randomly fail; the human
  verbally *diagnoses* failures while doing a secondary task (wordsearch).
- What it is NOT: voice *command* of a physical agricultural robot. No
  actuation from speech, no robot hardware, no command-success metrics.
  CONFIRMS the seed's characterization ("audio supervision of ag-robot
  fleets").

## 2. The absence statement (drafted for §II / §V positioning)

As of the 2026-07-04 sweep and dossier checks, we found no peer-reviewed
system that closes the loop **speech → command interpretation → physical
actuation on an agricultural robot → verified feedback**, evaluated
quantitatively. Nearest neighbours and why they miss:

| System | Misses |
|---|---|
| Audio ag-fleet supervision (RO-MAN'22, above) | audio is output-only (alerts); no voice command, no actuation, sim-only |
| Farming chatbots / advisory assistants (sweep) | no robot; conversation only |
| VoicePilot (Dossier #5) | assistive feeding, not agriculture |
| Generic voice-controlled mobile robots (Dossier #13) | not agriculture, no crop-care action set |

Wording rule: claim "we found no …" with the sweep documented (queries in
`comparison_candidates.md` §Sweep provenance + this dossier), never "there
exists no …". A single novelty sentence in the paper + this file as the
audit trail. This is a **positioning datum, not a comparison row** — exactly
as the seed list anticipated; no [EVAL-GAP] is created by it (nothing to
compare against is not a gap in OUR evaluation).

## 3. Verification steps performed

- Read RO-MAN 2022 paper full text (arXiv HTML): confirmed no voice-command
  capability (audio direction is robot→human; the human's verbal diagnosis
  is an experimental probe, not robot control).
- Sweep queries re-run against the seed's provenance list during dossier
  work surfaced no additional voice-controlled precision-agriculture robot
  with quantitative evaluation. (One adjacent find during Dossier #1 work:
  Merino-Fidalgo et al., RAS 2025 — LLM-generated BTs on a *social/home*
  robot, elderly-adjacent, NOT agriculture, and NOT in the seed list; listed
  in eval_strategy.md §additions-to-ask as a possible Front 1 extra pending
  approval.)

## 4. Fragility note (for the critic pass)

An absence claim ages badly and the sweep was snippet-based. Before
submission: one fresh search pass with the same queries + "voice-controlled
agricultural robot evaluation" variants, dated, appended here. Cheap
insurance for a claim a reviewer can falsify with one counterexample.
