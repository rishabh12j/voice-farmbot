# Dossier #1 — Gugliermo et al. 2024, "Evaluating behavior trees" (Front 0: the metrics source)

**Dossier status: VERIFIED against the full text.** PDF supplied by the author
(2026-07-05) and archived at
`documentation/eval/sources/gugliermo2024_evaluating_behavior_trees.pdf`.
This supersedes the 2026-07-04 partially-verified version; the access log
(§3) is retained as history. Ledger D1/D3 closed, D5 confirmed — see §5a.

---

## 1. Identity — VERIFIED (primary)

| Field | Value |
|---|---|
| Title | *Evaluating behavior trees* |
| Authors | Simona Gugliermo, David Cáceres Domínguez, Marco Iannotta, Todor Stoyanov, Erik Schaffernicht (first three equal contribution; AASS, Örebro University; Gugliermo also Scania CV AB) |
| Venue | Robotics and Autonomous Systems, Vol. 178, art. 104714, August 2024, Elsevier |
| DOI | 10.1016/j.robot.2024.104714 — open access CC-BY 4.0; 12 pages |
| Nature | Survey/consolidation: BT properties (§4), metrics (§3), property↔metric mapping (§5–6); proposes several NEW metrics of its own; "No data was used" (no experiments) |

## 2. What the paper is — VERIFIED (full text)

§3 "Metrics for behavior trees" (§3.1 Functional metrics) catalogues metrics
in use and proposes new ones (marked "(NEW)"); §4 defines properties
(Reactivity, Efficiency Def. 4.2, **Safety Def. 4.3**, Robustness,
Reliability, …); §5–6 map metrics to properties. There is no benchmark, no
experiment, no data — metric *definitions* are the only thing one can adopt
from it, which is exactly what we do.

## 3. Access log (historical — resolved 2026-07-05)

The 2026-07-04 dossier work could not obtain the full text from the
evaluation environment: ScienceDirect (Cloudflare bot-wall on HTML and PDF,
also via headless browser, jina.ai, Wayback), Elsevier API (key-gated), DiVA
(all nodes unreachable — outage/IP-block, incl. via third-party fetchers),
Unpaywall/OpenAlex/OpenAIRE/CORE/fatcat (publisher-only), citing documents
(no verbatim definitions). Resolved by the author supplying the CC-BY PDF via
institutional access. Kept as a record of why Run-1-era documents used
hedged attribution wording.

## 4. The attribution question — RESOLVED (verified locators)

- **The three metric NAMES and their intent ARE from this paper** (§3.1,
  pp. 2–3): "Desired Behavior Success Rate", "Single Node Success Rate",
  "Unsafe State Count" all appear as named metrics.
- **USC is the paper's own proposed metric** — it is marked "**(NEW)**" and
  the acronym **USC is used by the paper** (10 occurrences, math-italic).
  Citing Gugliermo et al. as the *origin* of USC is correct.
- **The acronyms "DBSR" and "SNSR" do NOT appear in the paper** (0
  occurrences, checked after Unicode normalization; the paper's symbol for
  Single Node Success Rate is SR_n). They are OUR abbreviations of THEIR
  metric names. Paper wording: "…which we abbreviate DBSR/SNSR."
  (This also explains the earlier negative web-search signal.)
- `thesis_chapter1_introduction.md` line 175 ("the DBSR/SNSR/USC metric
  definitions are from Gugliermo et al. (2024)") is now *approximately*
  right but must be revised to the §7 wording: names+intent theirs,
  operationalizations ours with disclosed deviations — because our
  formulations demonstrably differ (§5a).

## 5. Verbatim definitions from the paper (all §3.1, pp. 2–3)

- **Desired Behavior Success Rate**: "evaluates the extent to which the
  behavior executed by the BT aligns with the behavior expected by a
  behavior oracle. It focuses solely on the desired behavior while
  disregarding the internal mechanisms of the BT itself (e.g., return
  status)." Property-adaptable (safety ⇒ collision avoidance; correctness ⇒
  correct task execution); "the foremost and prevalent approach when
  referring to success rates"; explicitly includes single-evaluation cases.
- **Single Node Success Rate**: "the ratio between the number of successful
  completions of a node n ∈ N (i.e., when it returns Success) and the total
  number of executions of the node … SR_n = nS / (nS + nF)". **Per-node.**
- **Unsafe State Count (NEW)**: "assess and quantify the frequency with
  which the agent **enters** states that are considered unsafe … Given a set
  of unsafe states U, the USC metric is computed as the number of times the
  agent encounters states in U by the total number of states T visited
  during the execution: **USC = nU / nT**." Unsafe per **Definition 4.3
  (Safety)**: "The ability to avoid specific parts of the state space that
  can result in irreversible undesired behaviors."
- Execution time appears under §3.1 "Use of Resources": "the duration
  required for a BT to complete its execution", or per-node duration.
- No counterpart exists for our ELC (full-text scan: Behavior Usage
  Accuracy, Condition Check Frequency (NEW), Predictability Distance, Use of
  Resources, etc. — nothing event-log-based). ELC confirmed project-specific.

## 5a. Verified diff — paper formulations vs our implementations (`tools/evaluate_v2.py`)

| Metric | Paper (§3.1) | Ours | Deviation (ledger) |
|---|---|---|---|
| Desired Behavior Success Rate | behavior-oracle alignment; disregards internal mechanisms/return status | expected-command substrings in `commands_published` (external behavior; the corpus is the oracle) — BUT refusal/negation cases use Pi *terminal status* + zero commands, i.e. partially internal | name+intent theirs; substring proxy + status use disclosed (D6) |
| Single Node Success Rate | per-node ratio SR_n = nS/(nS+nF) | pooled micro-average over ALL leaves of all trees; contaminated by by-design CheckDry failures | **confirmed deviation** (D5); decision 2026-07-05: SNSR appendix-only |
| Unsafe State Count | normalized frequency nU/nT of **entered** unsafe states; general unsafe set U (Def. 4.3) | raw per-case count of guard-**blocked attempts**, detected by "out of bounds" message; bounds-only U | **confirmed 3-way deviation**: normalization; entered vs blocked-attempt; U restricted (D3/D4) |
| Latency | "execution time" (BT completion duration) under Use of Resources | end-to-end request latency incl. classification + real sim walks | broader scope; disclose (D23) |
| ELC | — (no counterpart) | event-log coverage | project-specific, confirmed (gap #4) |

## 6. Deviations ledger disposition

- **D1 CLOSED (verified)**: names+intent from the paper; USC acronym theirs;
  DBSR/SNSR abbreviations ours — one clause in §VI covers it.
- **D2 stands**: survey, no data — never imply a Gugliermo benchmark.
- **D3 CLOSED (confirmed as a real deviation)**: nU/nT vs raw count — now a
  *disclosed deviation*, no longer an open question.
- **D4 stands** (blocked-attempts semantics; stress-test split mandatory).
- **D5 confirmed** (per-node vs pooled) + SNSR moved to appendix (author
  decision 2026-07-05).
- **D6 refined**: add the return-status use in refusal/negation scoring as
  part of the disclosed DBSR operationalization.

## 7. Adopted §VI wording (verified, locatable — replaces all earlier hedges)

> "The metric names — Desired Behavior Success Rate, Single Node Success
> Rate, and Unsafe State Count — and their intent are from Gugliermo et al.
> [ref] (§3.1; USC is that paper's own proposed metric and acronym; DBSR and
> SNSR are our abbreviations). The corpus, harness, and operationalizations
> are ours, with disclosed deviations: their USC is a normalized frequency
> (nU/nT) of entered unsafe states (Def. 4.3), whereas we report entered
> unsafe states — structurally zero, since the guard chain fails a subtree
> before any motion command is published — separately from guard-blocked
> attempts, which we count per case; and their Single Node Success Rate is
> per-node (SR_n = nS/(nS+nF)), whereas ours pools all leaf nodes (reported
> in the appendix)."

The USC=0 headline keeps its construction-authority phrasing (zero entered
unsafe states by construction; zero unsafe *attempts* in Run 1; the stress
test reports guard activations separately).

## 8. Could / could not verify — final summary

| Item | Status |
|---|---|
| Identity, venue, CC-BY, survey nature, "no data used" | VERIFIED (primary) |
| Metric names + intent in the paper | **VERIFIED** (§3.1, pp. 2–3) |
| USC = nU/nT, entered states, Def. 4.3, "(NEW)", acronym | **VERIFIED** |
| SNSR per-node formula SR_n | **VERIFIED** |
| DBSR oracle-alignment definition | **VERIFIED** |
| "DBSR"/"SNSR" acronyms in the paper | VERIFIED ABSENT — ours |
| ELC counterpart | VERIFIED ABSENT — project-specific |
| Our implementations + Run 1 numbers | VERIFIED (unchanged) |

No open items remain for this dossier.
