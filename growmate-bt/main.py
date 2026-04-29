#!/usr/bin/env python3
"""
GrowMate MVP -- Behaviour Tree Edition

Full end-to-end voice assistant with transparent execution:
  Voice -> STT -> AI Core (constructs BT) -> BT Engine (executes) -> Robot/APIs/TTS

Usage:
    python main.py --text --model gemma3:4b       # Text mode
    python main.py --text --model gemma3n:e2b     # Lighter model
    python main.py --model gemma3:4b              # Voice mode
    python main.py --ros2 --model gemma3:4b       # Connected to real FarmBot
"""

import sys
import os
import argparse
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from growmate.ai_core import AICore
from growmate.bt_engine import BTEngine, NodeStatus
from growmate.speech import SpeechToText, TextToSpeech
from growmate.robot_controller import RobotController


class GrowMate:
    """The complete GrowMate system with behaviour tree execution."""

    def __init__(self, model="gemma3:4b", text_mode=False,
                 ros2_enabled=False, config_path=None, verbose=True):

        print("\n" + "=" * 60)
        print("  [GrowMate] GrowMate -- Behaviour Tree Voice Assistant")
        print("=" * 60)
        print()

        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "config", "farmbot.yaml")

        self.verbose = verbose
        print("  Initializing components...")

        # User Interaction Layer
        self.stt = None if text_mode else SpeechToText()
        self.tts = TextToSpeech()

        # AI Core -- constructs behaviour trees
        self.ai = AICore(config_path=config_path, model=model)
        if self.ai.is_available():
            print(f"  [LLM] AI Core: Connected (model: {model})")
        else:
            print(f"  [ERROR] AI Core: Ollama not running!")
            print(f"  [HINT] Run: ollama pull {model}")
            sys.exit(1)

        # Robot Controller
        self.robot = RobotController(ros2_enabled=ros2_enabled)

        # BT Engine -- executes behaviour trees
        self.bt = BTEngine(
            garden_config=self.ai.garden,
            robot_controller=self.robot
        )
        self.bt.llm_callback = self.ai.reason  # Wire LLM reasoning nodes
        print(f"  [BT] BT Engine: Ready ({len(self.bt.functions)} functions registered)")

        self.text_mode = text_mode
        self.running = True

        print()
        print("=" * 60)
        print("  Ready! Speak to your garden robot.")
        print("  Commands: 'quit' to exit, 'tree' to see last tree")
        print("=" * 60)
        print()

        self.tts.speak("GrowMate ready. How can I help with your garden today?")
        self.last_tree = None

    def run(self):
        """Main loop."""
        while self.running:
            try:
                user_input = self._get_input()
                if not user_input:
                    continue

                if user_input.lower().strip() in ('quit', 'exit', 'q', 'goodbye', 'bye'):
                    self.tts.speak("Goodbye! Happy gardening!")
                    break

                if user_input.lower().strip() == 'tree' and self.last_tree:
                    print("\n  Last behaviour tree:")
                    print("  " + json.dumps(self.last_tree, indent=2).replace("\n", "\n  "))
                    continue

                # Step 1: AI Core constructs a behaviour tree
                print(f"\n  [LLM] Constructing behaviour tree...")
                tree = self.ai.construct_tree(user_input)

                if tree is None:
                    self.tts.speak("Sorry, I couldn't figure out what to do. Could you try again?")
                    continue

                self.last_tree = tree

                # Step 2: Show the plan (transparency!)
                if self.verbose:
                    self._print_tree(tree)

                # Step 3: BT Engine executes the tree
                print(f"\n  [BT] Executing behaviour tree...")
                result = self.bt.execute(tree)

                # Step 4: Handle confirmation if needed
                if result.needs_confirmation:
                    self.tts.speak(result.confirmation_prompt)
                    answer = self._get_input()
                    if answer and answer.lower().strip() in ('yes', 'yeah', 'sure', 'go ahead', 'ok', 'y'):
                        # Re-execute without confirmation nodes (simplified: just execute commands)
                        for cmd in result.farmbot_commands:
                            self.robot.execute([cmd])
                        self.tts.speak("Done!")
                    else:
                        self.tts.speak("Okay, cancelled.")
                    continue

                # Step 5: Execute robot commands
                if result.farmbot_commands:
                    self.robot.execute(result.farmbot_commands)

                # Step 6: Print execution summary
                if self.verbose:
                    self._print_execution(result)

                # Step 7: Speak response
                if result.response_text:
                    self.tts.speak(result.response_text)
                elif result.success:
                    self.tts.speak("Done!")
                else:
                    self.tts.speak("Something went wrong. Please try again.")

            except KeyboardInterrupt:
                print("\n")
                self.tts.speak("Goodbye!")
                break

    def _get_input(self) -> str:
        if self.text_mode or self.stt is None:
            try:
                return input("\n  [MIC] You: ").strip()
            except EOFError:
                return ""
        else:
            print()
            return self.stt.listen()

    def _print_tree(self, tree: dict, indent: int = 0):
        """Print the behaviour tree in a visual format."""
        if indent == 0:
            print(f"\n  +- Behaviour Tree Plan --------------------")

        prefix = "  | " + "  " * indent
        node_type = tree.get("type", "?")

        if node_type in ("sequence", "selector"):
            symbol = "->" if node_type == "sequence" else "?"
            label = tree.get("label", node_type)
            print(f"{prefix}{symbol} {label}")
            for child in tree.get("children", []):
                self._print_tree(child, indent + 1)
        elif node_type == "robot_action":
            name = tree.get("name", "")
            params = tree.get("params", {})
            params_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else ""
            print(f"{prefix}[ROBOT] {name}({params_str})")
        elif node_type == "function_call":
            name = tree.get("name", "")
            store = tree.get("store_as", "")
            print(f"{prefix}[FUNC] {name}() -> ${store}")
        elif node_type == "llm_reason":
            q = tree.get("question", "")[:40]
            print(f"{prefix}[LLM] reason: \"{q}...\"")
        elif node_type == "respond":
            msg = tree.get("message", "")[:50]
            print(f"{prefix}[RESPOND] \"{msg}...\"")
        elif node_type == "condition":
            print(f"{prefix}[COND] {tree.get('name', '')}")
        elif node_type == "confirm":
            print(f"{prefix}[CONFIRM] {tree.get('message', '')[:40]}")
        elif node_type == "wait":
            print(f"{prefix}[WAIT] wait {tree.get('seconds', '?')}s")
        elif node_type == "set_var":
            print(f"{prefix}[VAR] {tree.get('name', '')} = {tree.get('value', '')}")
        else:
            print(f"{prefix}? {node_type}")

        if indent == 0:
            print(f"  |------------------------------------------")

    def _print_execution(self, result):
        """Print execution results."""
        print(f"\n  +- Execution Results ---------------------")
        print(f"  | Status: {'PASS Success' if result.success else '[ERROR] Failed'}")
        if result.farmbot_commands:
            print(f"  | FarmBot commands: {result.farmbot_commands}")
        print(f"  | Time: {result.total_time_ms:.0f}ms")
        for nr in result.node_results:
            status = "PASS" if nr.status == NodeStatus.SUCCESS else "FAIL" if nr.status == NodeStatus.FAILURE else "WAIT"
            if nr.message and not nr.message.startswith("Sequence"):
                print(f"  |   {status} {nr.message}")
        print(f"  |------------------------------------------")


def main():
    parser = argparse.ArgumentParser(description="GrowMate BT Voice Assistant")
    parser.add_argument("--text", action="store_true", help="Text input mode")
    parser.add_argument("--model", default="gemma3:4b", help="Ollama model")
    parser.add_argument("--ros2", action="store_true", help="Enable ROS2")
    parser.add_argument("--config", default=None, help="Garden config path")
    parser.add_argument("--quiet", action="store_true", help="Less verbose output")
    args = parser.parse_args()

    app = GrowMate(
        model=args.model, text_mode=args.text,
        ros2_enabled=args.ros2, config_path=args.config,
        verbose=not args.quiet
    )
    app.run()


if __name__ == "__main__":
    main()
