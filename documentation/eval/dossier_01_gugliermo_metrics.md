# Dossier #1 — Gugliermo et al. 2024, "Evaluating behavior trees" (Front 0: the metrics source)

**Dossier status: PARTIALLY VERIFIED — full text NOT obtained. Attribution of the
DBSR/SNSR/USC names to this paper is UNCONFIRMED and now looks doubtful.**

Date of dossier work: 2026-07-04. All locators live. This dossier separates,
explicitly, what was VERIFIED against a primary source, what rests on secondary
sources, and what could not be verified at all.

---

## 1. Identity — VERIFIED (primary: publisher page, DiVA record, Crossref, OpenAlex)

| Field | Value | Verified against |
|---|---|---|
| Title | *Evaluating behavior trees* | ScienceDirect article page; DiVA record diva2:1885757; Crossref |
| Authors | Simona Gugliermo, David Cáceres Domínguez, Marco Iannotta, Todor Stoyanov, Erik Schaffernicht (first three: equal contribution, fn.1) | ScienceDirect article page; DiVA |
| Venue | Robotics and Autonomous Systems, Vol. 178, art. 104714, August 2024, Elsevier | ScienceDirect; Crossref; OpenAlex |
| DOI | 10.1016/j.robot.2024.104714 | Crossref |
| Access | Open access, CC-BY 4.0 | ScienceDirect page; Crossref license field; Unpaywall (is_oa: true, publisher-hosted only) |
| Affiliation | AASS, Örebro University (+ Scania CV AB for Gugliermo) | DiVA record |
| No preprint | No arXiv/repository copy exists; DiVA record states "No full text in DiVA" | DiVA diva2:1885757; OpenAlex locations; Unpaywall |

The seed-list locator (`sciencedirect.com/science/article/pii/S0921889024000976`)
is correct — CONFIRMED.

## 2. What the paper is — VERIFIED at abstract level only

Verbatim abstract (captured 2026-07-04 from the ScienceDirect page):

> "Behavior trees (BTs) are increasingly popular in the robotics community. Yet
> in the growing body of published work on this topic, there is a lack of
> consensus on what to measure and how to quantify BTs when reporting results.
> This is not only due to the lack of standardized measures, but due to the
> sometimes ambiguous use of definitions to describe BT properties. This work
> provides a comprehensive overview of BT properties the community is
> interested in, how they relate to each other, the metrics currently used to
> measure BTs, and whether the metrics appropriately quantify those properties
> of interest. Finally, we provide the practitioner with a set of metrics to
> measure, as well as insights into the properties that can be derived from
> those metrics."

Two consequences that are solid even without the full text:

1. **It is a survey/consolidation of metrics, not a benchmark paper.** It
   surveys "metrics currently used" and recommends "a set of metrics to
   measure". There is no Gugliermo *protocol* (corpus, task suite, seeds) for
   us to be compared against — citing it buys metric *definitions* for
   comparability, not comparison *numbers*.
2. **The paper reports no experimental data of its own.** The article page
   carries the declaration "No data was used for the research described in the
   article" (Data availability section, captured verbatim 2026-07-04). Any
   claim that our numbers "follow the Gugliermo evaluation" can only ever mean
   "use metrics they define/recommend" — never "replicate their experiment".

Independent characterization by a citing paper (secondary, but consistent):
Ingrand-group formal-BT paper (arXiv 2502.11904v1, §2.1) — "the authors of
Gugliermo et al. (2024) propose a set of metrics (some static, some gathered
from real runs), to evaluate some BT properties". Captured verbatim 2026-07-04.

## 3. Access log — every route attempted for the full text (all failed)

Recorded for reproducibility and so nobody re-treads this. Attempted
2026-07-04 from the evaluation environment:

| Route | Result |
|---|---|
| ScienceDirect HTML article page (curl, headless Chromium/Playwright, jina.ai reader, crawl) | Cloudflare bot-wall / CAPTCHA; only the abstract + metadata shell is served |
| ScienceDirect PDF (`/pdfft` link, direct + via file-analysis tooling) | HTTP 403 |
| Elsevier Article Retrieval API (Crossref TDM links) | Serves coredata anonymously; full text requires an API key (`AUTHENTICATION_ERROR`) |
| DiVA (oru.diva-portal.org, kth.diva-portal.org, www.diva-portal.org) — incl. Gugliermo/Iannotta PhD theses that likely reproduce the paper | Network timeout on every node (host 130.238.7.111 unreachable, also from third-party fetchers — DiVA outage or datacenter-IP block) |
| Wayback Machine (article page snapshots 2025-03/2025-11/2026-03; CDX for `/pdfft` and DiVA PDFs) | Only the JS shell was archived (no body text); no PDF captures exist |
| Unpaywall / OpenAlex / OpenAIRE / CORE / fatcat / Semantic Scholar | All point back to the publisher page only; no mirrored PDF |
| Citing documents that quote definitions (IJCAI-25 #0969, arXiv 2502.11904, POLIMI Izzo 2024 thesis, UVa Merino-Fidalgo RAS paper) | Fetched and searched: none reproduces the DBSR/SNSR/USC definitions |

**Practical unblock (action item):** the author of this repo has institutional
access (Maynooth) to ScienceDirect. Downloading the 15-odd-page CC-BY PDF and
committing it (license permits redistribution with attribution) or placing it
under `documentation/eval/sources/` unblocks full verification in minutes.
Until then, every statement in §5 below stays flagged.

## 4. The attribution question — repo claim vs. evidence

What the repo claims:

- `tools/evaluate_v2.py` docstring (line 1): "measures the Gugliermo metrics".
- `documentation/thesis_chapter1_introduction.md` line 175: "the DBSR/SNSR/USC
  metric definitions are from Gugliermo et al. (2024); the corpus, harness and
  their application are mine, but the definitions are not."
- `README.md` line 151: "the Gugliermo et al. (2024) metric set".
- `demo/eval_v2_results.md` line 5: "V1 baseline reported in the thesis
  interim (Gugliermo et al. metrics)".

What the evidence says:

- **NOT VERIFIED** that the strings "Desired Behavior Success Rate" (DBSR),
  "Single Node Success Rate" (SNSR), or "Unsafe State Count" (USC) appear in
  the paper at all.
- **Negative signal (strong):** exhaustive exact-phrase web searches for
  "Desired Behavior Success Rate" and "Single Node Success Rate" (2026-07-04)
  return effectively **zero** uses of these as established BT-community terms —
  no papers, no docs, no theses surfaced using them. If the paper had coined
  these names, its 200+ citing works would have propagated them. The most
  plausible reading: **the acronyms are project-local names for metric
  *concepts* that the paper discusses under different or more general names**
  (task-level success rate; node-level success/failure statistics; safety /
  unsafe-states counting).
- **One secondary snippet, UNVERIFIED:** a DiVA-hosted Örebro thesis
  (unreachable during dossier work) is reported by search indexing to define,
  citing this paper, an unsafe-state metric as *count of encounters with states
  in an unsafe set U divided by total states T visited by the agent* — i.e., a
  **normalized frequency**, not a raw count. If that is the paper's
  formulation, our raw-count USC differs in normalization AND in unit of
  counting (see ledger D3/D4). Do not cite this until the primary text is read.

**Recommendation for the paper (§VI):** until the full text is verified, the
honest sentence is *"we adopt metrics in the spirit of the evaluation-practice
consolidation of Gugliermo et al. [ref], operationalized for our pipeline as
follows: …"* — and then define DBSR/SNSR/USC explicitly as ours. Claiming "the
definitions are from Gugliermo et al." (thesis_chapter1 line 175) is currently
an **unverified citation claim** and, on present evidence, likely wrong in the
strict sense. This exact sentence must be revised or verified.

## 5. OUR implementations — VERIFIED directly against code (locators exact)

Source: `tools/evaluate_v2.py` (docstring lines 21–35; scoring lines 420–509)
and the Pi-side guard `src/growmate_pi/bt/condition_nodes.py` (CheckBounds,
lines 102–129). Run numbers: `demo/eval_v2_results.md` Run 1 (2026-07-04,
SIM-ONLY).

| Metric | Our operational definition (exact) | Code locator |
|---|---|---|
| DBSR | % of executed cases that "pass" (Run 1: 42 of the 43 authored cases — `--skip-long` excluded the one `safety`-category long walk; ledger D26). Pass means: (a) cases with expected commands — every expected command substring appears in some entry of the Pi's `commands_published` (substring match; `@move:<species>` templates resolved against the live map pre-run); (b) refusal/negation cases — Pi terminal status `success` AND zero commands; (c) no-command cases (general) — Pi status in {success, partial} | `evaluate_v2.py` 429–438, 492, 502 |
| SNSR | Pooled micro-average: (Σ leaf nodes with status `success`) / (Σ all leaf `node_results`) across every tree the run executed | `evaluate_v2.py` 423–424, 493–494, 503 |
| USC | Count of **cases** in which any node's failure message contains the substring "out of bounds" | `evaluate_v2.py` 425–427, 495, 504 |
| ELC | % of applicable cases (expected an event AND Pi reported success/partial) whose expected event rows all appear in the Pi's SQLite event log; V2-only, no prior-work counterpart | `evaluate_v2.py` 443–456, 498–505 |
| Latency | Mean of the Pi-reported wall-clock `duration_ms` per request (plus max) | `evaluate_v2.py` 496, 507–508 |

Semantics that any cross-paper comparison must carry:

- **USC counts blocked attempts, not entered states.** The only source of an
  "out of bounds" message is the `CheckBounds` guard
  (`condition_nodes.py:126`), which FAILS the subtree **before** `MoveTo`
  publishes anything (docstring: "the last guard before a MoveTo is allowed to
  publish"). The pipeline therefore cannot *enter* an out-of-bounds state at
  all; USC increments when a case *attempts* one and is refused. USC = 0 in
  Run 1 means "no case even attempted an out-of-bounds target" — grounding
  resolves targets from the live map, which contains only in-bounds plants.
  This is stronger than "violations were caught", but it also means the
  measured USC=0 primarily certifies the *grounding* layer, and the guard
  itself is exercised only if an attempt occurs. The misclassification stress
  test must be designed to hit the guard, not just the grounder (see
  eval_strategy.md).
- **USC's unsafe-state universe is bounds-only.** Nothing else counts as
  unsafe: wrong-plant watering, tool-mounted hazards, e-stop bypasses are all
  invisible to USC as implemented. (Wrong-plant actions are penalized by DBSR,
  not USC; negation violations are penalized by DBSR too.) A paper sentence
  that says "no unsafe states" must say "no workspace-bounds violations".
- **SNSR carries a by-design depressor.** Run 1 SNSR = 91.2% includes
  `water_smart` `CheckDry` condition leaves that fail intentionally for moist
  plants under a Selector (documented in `demo/eval_v2_results.md`,
  "Regressions or surprises"). Any external SNSR-like comparison is
  meaningless unless failed-by-design condition leaves are footnoted or
  excluded.
- **DBSR is substring-proxy task success**, evaluated below STT (text in), on
  a self-authored 42-case corpus, with the LLM classification in the loop and
  live-map grounding — an end-to-end *pipeline* success rate, not a BT-quality
  measure in isolation.

## 6. Deviations ledger entries seeded by this dossier

(Ledger lives in `eval_strategy.md`; entries D1–D6 originate here.)

- **D1 (attribution).** "DBSR/SNSR/USC definitions are from Gugliermo et al."
  is UNVERIFIED; exact-name existence in the source is doubtful. Until the PDF
  is read: cite as "metrics adopted in the spirit of [Gugliermo-24]" and
  define ours explicitly. Affected artifacts: `evaluate_v2.py` docstring,
  README §Evaluation, thesis_chapter1 line 175, thesis interim, eval doc.
- **D2 (paper type).** The source is a metrics survey with no experiment and
  no data; no number in it can ever be a comparison row. Consumers must not
  imply a "Gugliermo benchmark".
- **D3 (USC normalization — conditional).** Secondary-source signal that the
  survey's unsafe-state metric is a frequency (encounters ÷ states visited),
  ours is a raw per-case count. UNVERIFIED; resolve after PDF access.
- **D4 (USC unit + detection).** Ours counts *cases* (not encounters), detects
  via message-substring "out of bounds" only, and counts guard-blocked
  *attempts* — the system cannot enter the unsafe state by construction. Any
  comparison with a system that counts entered unsafe states is
  apples-to-oranges and must be bridged in text.
- **D5 (SNSR pooling + by-design failures).** Pooled leaf-level
  micro-average, contaminated by intentional condition-leaf failures
  (water_smart/CheckDry). Exclude-or-footnote rule applies to every quoted
  SNSR number.
- **D6 (DBSR proxy).** Substring-match on published commands; refusal/negation
  scored as success-with-zero-commands. This makes DBSR partly a *refusal
  quality* metric — unusual, and exactly the property we care about, but it
  must be described, not assumed.

## 7. What this means for the USC = 0 headline

The headline survives, but its wording must change from metric-authority to
construction-authority:

- Not: "USC = 0 on the Gugliermo et al. metric" (unverified attribution,
  doubtful naming, different counting semantics).
- But: "Zero workspace-bounds violations (USC = 0) across all 42 cases and
  [pending] N forced misclassifications; by construction the deterministic
  guard chain (`CheckAvailable → [CheckToolMounted] → [CheckBounds] →
  [CheckPlantFound]`) fails a subtree before any motion command is published,
  so a wrong intent degrades to a visible, safe no-op."

That sentence is fully supported by verified code + Run 1 data and needs no
external metric authority. The Gugliermo citation then supports the *practice*
of reporting explicit success/safety metrics, which is what the paper actually
argues for (per its verified abstract).

## 8. Could / could not verify — summary

| Item | Status |
|---|---|
| Paper exists, venue/authors/DOI/OA status | VERIFIED (primary) |
| Survey nature; "no data used" declaration | VERIFIED (primary, abstract + article page) |
| "Set of metrics, some static, some from runs" | Secondary (citing paper), consistent |
| DBSR/SNSR/USC names appear in the paper | **NOT VERIFIED — doubtful** (strong negative web signal) |
| Exact metric formulas in the paper | **NOT VERIFIED** (full text inaccessible from this environment) |
| Our implementations of DBSR/SNSR/USC/ELC/latency | VERIFIED (code read, locators above) |
| Run 1 numbers (DBSR 100.0, SNSR 91.2, USC 0, ELC 100.0 n=27, mean 9331 ms) | VERIFIED against `demo/eval_v2_results.md` (SIM-ONLY, 2026-07-04) |

**Blocking action item for the author:** obtain the PDF via institutional
access; then §4/§5 mismatches get resolved into hard ledger entries within
minutes. Everything else in this dossier stands regardless.

---

## 9. PDF VERIFICATION — 2026-07-04 (CLOSES D1–D3). Full text read.

The author obtained the CC-BY PDF via institutional access; it is committed at
`documentation/eval/sources/gugliermo2024_evaluating_behavior_trees.pdf`
(12 pages). Full-text search + passage reading. **This section supersedes the
"doubtful" verdicts of §4; the audit trail above is retained deliberately.**

### D1 — RESOLVED: the attribution is CORRECT. The names ARE the paper's.

| Name | Occurrences | Evidence (verbatim) | Locator |
|---|---|---|---|
| Desired Behavior Success Rate | 8 | "the global or overall success of execution, which **we define as** the Desired Behavior Success Rate" | §3 (Success Rate), p. 2–3 |
| Single Node Success Rate | 7 | "**Single Node Success Rate** refers to the ratio between the number of successful completions of a node n ∈ N … SR_n = nS / (nS + nF)" | §3, p. 3 |
| Unsafe State Count | 3 (+1 "USC") | "**Unsafe State Count (NEW)**" — introduced BY this paper as a new metric | §3, p. 3 |

The §4 negative web signal was an artifact: the full text is invisible to
crawlers (bot-wall), and citing papers did not propagate the names. The
thesis_chapter1 L175 attribution ("the definitions are from Gugliermo et
al.") is **vindicated for the names and metric intent** — with the
operationalization differences below still requiring disclosure.

### D3 — RESOLVED: their USC IS a normalized frequency (ours is not)

Verbatim (p. 3): *"Given a set of unsafe states U, the USC metric is computed
as the number of times the agent encounters states in U by the total number
of states T visited during the execution: USC = nU / nT."* A state is unsafe
"when it leads to irreversible undesired behaviors" (Definition 4.3).

Diff vs ours (D4 refined, still open as a disclosed deviation):
- **Unit:** theirs = state-visits; ours = corpus cases.
- **Normalization:** theirs = ratio nU/nT; ours = raw count.
- **Semantics:** theirs counts ENTERED unsafe states; ours counts
  guard-BLOCKED attempts (the system cannot enter one by construction).
- **Compatibility of the headline:** since no unsafe state is ever entered,
  our system's USC under THEIR definition is 0/nT = 0. "USC = 0" is true
  under both semantics; the paper must state ours is the stricter
  attempted-and-blocked reading, theirs the entered-state frequency.

### Definition diffs for the other two (feed §VI wording)

- **DBSR (theirs):** "evaluates the extent to which the behavior executed by
  the BT aligns with the behavior expected by a **behavior oracle**…
  disregarding the internal mechanisms of the BT itself (e.g., return
  status)"; explicitly adaptable per property (for safety, desired behavior =
  avoidance of unsafe states). OURS — expected-command substrings against
  commands_published, refusals scored as success-with-zero-commands — is a
  faithful operationalization: the expected-command list IS the oracle.
- **SNSR (theirs):** per-node ratio SR_n = nS/(nS+nF). OURS pools all leaves
  of all trees into one micro-average — an aggregation deviation to disclose
  alongside the CheckDry by-design contamination (D5).

### Bonus methodological anchor (use in §VI)

For safety evaluation the paper prescribes exactly our stress-test design:
DBSR "must be evaluated in scenarios that can potentially bring the BT to
unsafe states", citing Colledanchise et al. [23] "artificially injecting
faults in the system" (p. 6, Safety/Evaluation). The misclassification stress
harness (tools/stress_misclassification.py, 120 injections, unsafe-motion 0)
is therefore not merely inspired by this paper — it implements its
recommended safety-evaluation practice. Also note their caveat, to carry
verbatim into §VI: "these metrics cannot guarantee safety."
