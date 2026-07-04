# Paste-ready wording: metrics attribution + §VI Metrics subsection

Prepared 2026-07-05 from the verified Dossier #1 (§5a/§7). Everything below is
backed by the archived PDF
(`documentation/eval/sources/gugliermo2024_evaluating_behavior_trees.pdf`)
and `tools/evaluate_v2.py` / `demo/eval_v2_results.md` (Run 1, SIM-ONLY).
Apply by hand; the eval task's ground rules keep the agent out of the thesis
files.

---

## 1. Drop-in fix — `documentation/thesis_chapter1_introduction.md` (§1.6, "not my own work" list)

**REPLACE this bullet (currently line ~175):**

> - **Evaluation metrics** — the DBSR/SNSR/USC metric definitions are from
>   Gugliermo et al. (2024); the corpus, harness and their application are
>   mine, but the definitions are not.

**WITH:**

> - **Evaluation metrics** — the metric names and their intent (Desired
>   Behavior Success Rate, Single Node Success Rate, Unsafe State Count) are
>   from Gugliermo et al. (2024, §3.1); USC is that paper's own proposed
>   metric and acronym, while "DBSR" and "SNSR" are my abbreviations of
>   their metric names. The corpus, harness, and operationalizations are
>   mine, with two disclosed deviations from their formulations: their USC
>   is a normalized frequency (nU/nT) of *entered* unsafe states, whereas I
>   report entered unsafe states (structurally zero — the guard chain fails
>   a subtree before any motion command is published) separately from
>   guard-blocked attempts, counted per case; and their Single Node Success
>   Rate is per-node (SR_n = nS/(nS+nF)), whereas mine pools all leaf nodes
>   and is reported in the appendix only.

---

## 2. §VI Metrics subsection — paste-ready draft

### VI-x. Metrics

We adopt the metric names and intent of the behavior-tree evaluation
consolidation of Gugliermo et al. [ref-Gugliermo24]. Unsafe State Count
(USC) is that work's own proposed metric and acronym; Desired Behavior
Success Rate and Single Node Success Rate appear there as named metrics,
which we abbreviate DBSR and SNSR. Our corpus, harness, and
operationalizations are our own, and deviate from the source formulations
in two disclosed respects stated below. All metrics are computed by the
evaluation harness (`tools/evaluate_v2.py`) from the robot-side execution
record: the published command stream, per-node tick results, and the
firmware-verified event log.

**DBSR (task-level success).** Gugliermo et al. define Desired Behavior
Success Rate as the degree to which the executed behavior aligns with the
expectation of a behavior oracle, independent of the tree's internal return
statuses. Our oracle is the evaluation corpus: a case passes when every
expected command appears in the commands the robot controller actually
published; refusal and negation cases pass only when the pipeline terminates
successfully having published *zero* commands and spoken a refusal. The
zero-command check inspects the terminal status of the tree, a deliberate
departure from the source's internal-mechanism exclusion, required to score
clean refusals.

**USC (safety).** Gugliermo et al. define USC as the normalized frequency
nU/nT with which the agent *enters* states from an unsafe set U, where a
state is unsafe if it can lead to irreversible undesired behavior (their
Definition 4.3). In our architecture the guard chain (availability → tool →
workspace bounds → target-exists) fails a subtree before any motion command
is published, so unsafe states cannot be entered through the voice pipeline;
entered-state USC is therefore structurally zero, and we report it
separately from *guard-blocked attempts*, which we count per case. In the
evaluation corpus U is operationalized as workspace-bounds violations; USC
does not measure task correctness (a misclassified but in-bounds action is
counted against DBSR, not USC).

**ELC (verified feedback).** Event Log Coverage is project-specific (no
counterpart exists in [ref-Gugliermo24] or, to our knowledge, the compared
systems): the fraction of applicable cases whose expected effects all appear
in the robot's firmware-verified event log — the metric behind the
honest-feedback requirement, distinguishing *verified* completion from
claimed completion.

**Latency.** Mean and maximum wall-clock duration per request, end-to-end
(classification, grounding, tree synthesis, and execution including physical
motion). We report classification latency separately where model comparisons
are made, since execution time is dominated by motion, not inference.

**SNSR (appendix).** Gugliermo et al.'s Single Node Success Rate is
per-node, SR_n = nS/(nS+nF). Our variant pools all leaf nodes across all
executed trees into a single micro-average and is additionally depressed by
condition leaves that fail *by design* (moisture checks under a fallback
node for already-moist plants). Because the pooled figure is easily
misread, SNSR is reported in the appendix only, with the failed-by-design
leaves footnoted.

**Corpus and scope.** The corpus comprises 43 authored cases across ten
categories (direct 10, hard 10, indirect 4, emergency 4, refusal 4, query 3,
general 3, multi 2, negation 2, safety 1); Run 1 executed 42, excluding the
single long-walk safety case. The corpus was developed alongside the system
and served as its regression suite; it is a validation corpus, not a
held-out benchmark. All Run 1 numbers are simulation-only; hardware
replication (Run 2) is [pending/reported in §VII].

---

## 3. §VII phrasing snippets (ready to reuse)

**The USC headline (construction-authority form):**

> Across all 42 executed cases (and [pending] N forced misclassifications in
> the stress test), zero workspace-bounds violations occurred (USC = 0) and
> zero unsafe motions were published. By construction, the deterministic
> guard chain fails a subtree before any motion command is emitted, so a
> wrong intent degrades to a visible, safe no-op; guard activations are
> reported separately from entered unsafe states.

**The regime-comparison disclaimer (goes above any comparison table):**

> Prior-work figures are reported under each system's own protocol and are
> not directly comparable to ours; the table contrasts evaluation *regimes*,
> and every figure carries its setting.

---

## 4. Reference entry

Plain (IEEE-style):

> S. Gugliermo, D. Cáceres Domínguez, M. Iannotta, T. Stoyanov, and
> E. Schaffernicht, "Evaluating behavior trees," *Robotics and Autonomous
> Systems*, vol. 178, art. 104714, Aug. 2024, doi: 10.1016/j.robot.2024.104714.

BibTeX:

```bibtex
@article{gugliermo2024evaluating,
  title   = {Evaluating behavior trees},
  author  = {Gugliermo, Simona and C{\'a}ceres Dom{\'i}nguez, David and
             Iannotta, Marco and Stoyanov, Todor and Schaffernicht, Erik},
  journal = {Robotics and Autonomous Systems},
  volume  = {178},
  pages   = {104714},
  year    = {2024},
  doi     = {10.1016/j.robot.2024.104714}
}
```

---

## 5. Where else the old wording appears (check while editing)

- `tools/evaluate_v2.py` line 1 docstring — "measures the Gugliermo metrics"
  (code comment; harmless, but if touched during other work, prefer
  "measures the metric set adopted from Gugliermo et al. (2024), see §VI").
  Do NOT edit for this alone.
- `README.md` §Evaluation ("the Gugliermo et al. (2024) metric set") —
  acceptable shorthand; optionally align with the new wording.
- `demo/eval_v2_results.md` line 5 ("thesis interim (Gugliermo et al.
  metrics)") — historical record; leave as-is.
- Thesis interim — if it printed the old "definitions are from" sentence,
  the final report supersedes it with §1 above; no retroactive edit needed.
