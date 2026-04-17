"""Pure-CLI entry point, no ROS2 by default.

This is the desktop-development and paper-evaluation path. It runs the
exact same AICore + BTEngine pipeline as the real robot, but defaults to
simulation mode so you don't need a sourced ROS2 workspace or a physical
FarmBot on the network. Useful when you're iterating on prompts, the
node library, or the test corpus.

Usage::

    python -m growmate_voice.cli --model gemma3:4b
    # or, after colcon install:
    ros2 run growmate_voice voice_cli
"""

from __future__ import annotations

import sys

from .voice_controller_node import main as _node_main


def main(argv=None) -> None:
    # Inject --no-ros2 unless the user has already set it.
    if argv is None:
        argv = sys.argv[1:]
    if "--no-ros2" not in argv:
        argv = list(argv) + ["--no-ros2"]
    _node_main(argv)


if __name__ == "__main__":
    main()
