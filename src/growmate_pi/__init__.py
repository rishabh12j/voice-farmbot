"""GrowMate Pi — receives intent JSON from clients, builds and executes py_trees behaviour trees on the FarmBot.

The phone (or any client) handles STT and LLM intent classification, then POSTs
a structured intent payload to this package's FastAPI endpoint. The Pi
constructs a py_trees behaviour tree from the intent, ticks it, and publishes
FarmBot command strings to `keyboard_topic`.

This package is in early scaffolding — only the wire-format schemas live here
right now. See PLAN_CHANGES.md for the full migration plan.
"""

__version__ = "0.1.0"
