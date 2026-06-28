@AGENTS.md

# Claude-specific notes

The shared project context — the research contract (invariants that must not be
broken), repo layout, conventions, and workflow — is imported from `AGENTS.md`
above and is binding. Below is only Claude-specific behaviour.

- **Verify before you commit.** Run the sim harness and expect `Failures: 0/N`
  before committing/pushing any builder/node/schema change. On Windows it runs
  under WSL:
  `wsl -d Ubuntu-22.04 -- bash -lc "cd <repo> && PYTHONPATH=src ./venv-wsl/bin/python3 -m growmate_pi.verify_sim"`.
- **Use plan mode for wide-blast-radius edits** — `schemas.py` (the wire
  contract), any AURA-stack package, or the BT safety prefix. Propose before
  refactoring.
- **Run long tasks in the background.** `verify_sim` is slow (the `water_all`
  scenario pulses 54 plants in real time); use background execution rather than
  blocking.
- Keep edits **additive to AURA**; if an upstream package must change, make it a
  minimal, clearly-labelled bugfix in the commit message.
