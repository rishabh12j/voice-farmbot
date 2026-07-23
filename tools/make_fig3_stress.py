"""Regenerate documentation/paper/fig3_stress.pdf from a stress-test JSON.

The original figure script was not preserved; this reproduces its style (a
horizontal stacked bar per attack category, one segment per safe-outcome bucket)
so the stress figure is reproducible from the frozen JSON the harness writes.

Usage:
    python tools/make_fig3_stress.py documentation/eval/stress_2026-07-23.json \
        [documentation/paper/fig3_stress.pdf]
"""
import json
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Category key (harness) -> display label, in top-to-bottom plot order.
CATS = [
    ("oob_coords", "Out-of-bounds coords"),
    ("ghost_target", "Unplanted target"),
    ("malformed", "Malformed intent"),
    ("wrong_action", "Wrong action verb"),
    ("contradiction", "Negation ignored"),
    ("wrong_planted", "Wrong (real) plant"),
]
# Outcome -> colour (CVD-aware: blue/green/grey/orange, red reserved for unsafe).
OUTCOMES = [
    ("guard-blocked", "#1f77b4"),
    ("refused-clean", "#2ca02c"),
    ("failed-safe", "#7f7f7f"),
    ("wrong-but-bounded", "#e69f00"),
    ("unsafe-motion", "#d55e00"),
    ("anomaly", "#cc79a7"),
]


def main() -> int:
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "documentation/paper/fig3_stress.pdf"
    data = json.load(open(src, encoding="utf-8"))
    cases = data["cases"]
    summary = data.get("summary") or {}

    # Build per-category outcome counts (prefer summary; fall back to cases).
    def counts(cat_key):
        if cat_key in summary:
            return summary[cat_key]
        return dict(Counter(c["outcome"] for c in cases if c["category"] == cat_key))

    total = len(cases)
    unsafe = sum(1 for c in cases if c["outcome"] == "unsafe-motion")
    dishonest = sum(1 for c in cases if not c.get("honesty_ok", True))

    labels = [lbl for _, lbl in CATS]
    y = range(len(CATS))
    # only draw outcomes that actually occur, keeping legend order
    present = [(o, col) for o, col in OUTCOMES
               if any(counts(k).get(o, 0) for k, _ in CATS)]

    fig, ax = plt.subplots(figsize=(9.2, 4.0))
    left = [0] * len(CATS)
    for outcome, colour in present:
        vals = [counts(k).get(outcome, 0) for k, _ in CATS]
        ax.barh(list(y), vals, left=left, color=colour, label=outcome,
                height=0.62, edgecolor="white", linewidth=0.5)
        for i, v in enumerate(vals):
            if v > 0:
                txt = "white" if colour in ("#1f77b4", "#2ca02c", "#7f7f7f",
                                            "#d55e00", "#cc79a7") else "black"
                ax.text(left[i] + v / 2, i, str(v), ha="center", va="center",
                        color=txt, fontsize=10, fontweight="bold")
        left = [l + v for l, v in zip(left, vals)]

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=11)
    ax.invert_yaxis()
    per_cat = max(left) if left else 20
    ax.set_xlabel(f"Injections per attack category "
                  f"({per_cat} each, {total} total)", fontsize=11)
    ax.set_xlim(0, per_cat)
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)

    ax.set_title(f"Unsafe motions: {unsafe} / {total}      "
                 f"Honesty violations: {dishonest}",
                 fontsize=13, fontweight="bold", loc="left", pad=24)
    ax.text(0, 1.04, "Forced misclassifications injected below the classifier; "
            "outcomes scored from published commands (simulation).",
            transform=ax.transAxes, fontsize=10, color="#444")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=len(present),
              frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    print(f"wrote {out}")
    print(f"  total={total} unsafe={unsafe} dishonest={dishonest}")
    for k, lbl in CATS:
        print(f"  {lbl:<22} {counts(k)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
