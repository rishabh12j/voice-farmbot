"""py_trees node library, intent-to-tree builder, and tree executor.

Submodules:

* ``condition_nodes``: read-only checks (CheckAvailable, CheckBounds,
  CheckPlantFound). Return SUCCESS/FAILURE only.
* ``action_nodes``: do something — publish FarmBot commands, wait,
  resolve a target, record TTS text. May return RUNNING.
* ``builder``: maps a single ``Intent`` to a py_trees subtree, and a list
  of ``Intent`` objects to one flattened Sequence root.
* ``executor``: ticks a tree to completion and returns a ``TreeResult``.
"""
