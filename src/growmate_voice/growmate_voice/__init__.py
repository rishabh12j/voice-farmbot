"""GrowMate Voice — voice control for the AURA FarmBot ROS2 stack.

A drop-in replacement for the keyboard_controller that takes natural speech,
classifies intent with an on-device LLM, builds an inspectable behaviour tree,
validates safety per node, and publishes the resulting commands to
``keyboard_topic`` — the same topic the existing keyboard_controller uses.
"""

__version__ = "0.1.0"
