# CLAUDE.md

This project's agent context, architecture, the research contract (invariants
that must not be broken), conventions, and dev/run workflow live in
**[AGENTS.md](AGENTS.md)** — a single source of truth shared by all agent
tooling. Read it first.

Quick reminders (full detail in AGENTS.md):

- **The LLM only does flat intent classification.** All structure + safety live
  in the deterministic behaviour tree. New capability = one `schemas.Action`
  value + one `_tree_*` builder with the safety prefix — never new LLM structure.
- **Sim-verify before hardware:** `PYTHONPATH=src python3 -m growmate_pi.verify_sim`
  (expect `Failures: 0/N`).
- **Additive to the AURA stack** — publish only to `keyboard_topic`; edit
  upstream packages only for minimal, documented bugfixes.
- Keep **USC (unsafe-state count) = 0**; honour the **honest-or-blank** rule
  (nothing logged/spoken as done before firmware confirmation).
