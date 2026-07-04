# Dossier #8 — User Trust after Voice Assistant Failures (Front 3: trust-erosion mechanism)

**Dossier status: VERIFIED against full text (ar5iv render). ACCEPT as the
trust-after-failure mechanism citation. Seed locator corrected.**

Read 2026-07-04 from arXiv 2303.00164 (v2, 2023-03-03).

## 1. Identity — VERIFIED (seed locator corrected)

- Baughan (U. Washington), Mercurio (Google), et al., "A Mixed-Methods
  Approach to Understanding User Trust after Voice Assistant Failures",
  **ACM CHI 2023** (arXiv comments: "To appear in ACM CHI '23"; DOI
  10.1145/3544548.3581152). arXiv:2303.00164.
- The seed list's locator "ResearchGate 370203002" resolves to this paper;
  the RG entry is a mirror. Peer-reviewed venue question from the seed:
  ANSWERED — CHI 2023.

## 2. What it is — VERIFIED

Mixed-methods: crowdsourced dataset of **199 VA failures categorized across
12 failure sources** nested in **4 failure types** (attention, perception,
understanding, response); ~107 crowd contributors; 12 interviews; survey
(~268 participants) measuring trust (ability / benevolence / integrity
dimensions) per failure source.

## 3. Findings relevant to us — VERIFIED

- Failures from **overcapturing user input derail trust most** (abstract +
  §6): privacy-adjacent capture errors are the worst trust killers.
- Users **stop using the failing task for a while, then resume similar
  usage**: trust damage is task-specific and partially recoverable.
- **Low-stakes tasks (music) are how users rebuild trust** after failures.
- Most frequent in the wild: "no understanding" and "action execution:
  incorrect" failures.
- Not older-adult-specific: general adult population. Use it for the trust
  *mechanism*; use Dossiers #6/#7 for the older-adult specifics.

## 4. Comparability bridge / how we may use it

- Motivates the honest-or-blank rule: "action execution: incorrect" (acting
  on a misunderstanding) is among the most frequent failure sources —
  precisely what USC=0 + verified "done" messages target when the assistant
  controls a physical robot.
- The task-specific trust recovery finding gives design weight to clean
  refusals: a refusal is a *non-event* for trust compared to a wrong action.
  (Their data is about VAs, not robots — say so; stakes argument is ours.)
- Overcapture finding supports the on-device/privacy row of the constraint
  table (speech never leaves the device ⇒ the worst trust-erosion class is
  structurally reduced). Careful: overcapture is about accidental recording;
  on-device processing mitigates the *consequence*, not the capture itself.

## 5. Deviations ledger entries seeded

- **D14**: general-population VA study; not robots, not older adults. Cite
  for mechanism only; pair with [VA-Errors-24] for the population and with
  the physical-stakes argument for robots (ours, argued, not measured).

## 6. Not verified / open

- Full author list from arXiv metadata for references.
- Regression betas per failure source were not transcribed — re-read §6 if a
  specific coefficient is ever quoted.
