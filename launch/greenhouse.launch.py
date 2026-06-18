"""Single launch for one greenhouse's Pi stack.

Brings up the whole per-greenhouse stack in one command:

  1. farmbot_bringup (no_camera) — the AURA control stack + firmware link
  2. the growmate_pi intent server (run from the venv: fastapi / py_trees)
  3. the daily watering scheduler (waters_all + go_home at schedule.watering_time)

The intent server and scheduler are launched with the *venv* python (it has
fastapi/py_trees/httpx; with --system-site-packages it also sees rclpy) while
inheriting the sourced ROS 2 environment, so a single `ros2 launch` ties the
ROS-side bringup and the venv-side services together.

Run (after sourcing ROS 2 + the Rishabh workspace IN THE RIGHT ORDER — see
RUN_GUIDE 1.3a):

    ros2 launch ./launch/greenhouse.launch.py

Override paths/port if your layout differs, e.g.:

    ros2 launch ./launch/greenhouse.launch.py \
        venv_python:=/home/gh1/Rishabh_Growmate_FarmBot/venv/bin/python \
        src:=/home/gh1/Rishabh_Growmate_FarmBot/src port:=8000

If the firmware isn't publishing /busy_state yet, start the intent server with
the gate off instead (run it by hand with --no-verify) — see
demo/verify_gate_hardware.md.
"""

import os

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
    TimerAction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
    PathJoinSubstitution,
)
from launch_ros.substitutions import FindPackageShare

# launch/ sits directly under the repo root; derive venv + src defaults.
_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def generate_launch_description() -> LaunchDescription:
    venv_python = LaunchConfiguration("venv_python")
    src = LaunchConfiguration("src")
    port = LaunchConfiguration("port")

    # PREPEND src to the inherited PYTHONPATH — do NOT replace it, or we lose the
    # ROS python path (rclpy) and the intent server silently falls back to sim.
    pythonpath = [src, ":", EnvironmentVariable("PYTHONPATH", default_value="")]

    args = [
        DeclareLaunchArgument(
            "venv_python",
            default_value=os.path.join(_REPO, "venv", "bin", "python"),
            description="Python inside the venv (fastapi / py_trees / httpx).",
        ),
        DeclareLaunchArgument(
            "src",
            default_value=os.path.join(_REPO, "src"),
            description="PYTHONPATH root for growmate_pi.",
        ),
        DeclareLaunchArgument(
            "port", default_value="8000",
            description="Intent server port (scheduler talks to localhost:port).",
        ),
        DeclareLaunchArgument(
            "scheduler", default_value="true",
            description="Run the daily watering scheduler. Set false to bring up "
                        "bringup + intent server only (e.g. while testing tools).",
        ),
    ]

    # 1. AURA bringup (no camera — watering doesn't need vision).
    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare("farmbot_bringup"), "launch", "no_camera.launch.py",
            ])
        ])
    )

    # 2. Intent server — venv python, PYTHONPATH=src, inheriting the ROS env.
    #    Delayed so bringup has a head start.
    intent_server = TimerAction(period=8.0, actions=[
        ExecuteProcess(
            cmd=[venv_python, "-m", "growmate_pi.intent_server", "--port", port],
            additional_env={"PYTHONPATH": pythonpath},
            name="growmate_intent_server",
            output="screen",
        )
    ])

    # 3. Daily watering scheduler — talks to the local intent server.
    #    Skipped when scheduler:=false (bringup + intent server only).
    scheduler = TimerAction(period=20.0, actions=[
        ExecuteProcess(
            cmd=[venv_python, "-m", "growmate_pi.scheduler",
                 "--intent-url", ["http://localhost:", port]],
            additional_env={"PYTHONPATH": pythonpath},
            name="growmate_scheduler",
            output="screen",
            condition=IfCondition(LaunchConfiguration("scheduler")),
        )
    ])

    return LaunchDescription(args + [bringup, intent_server, scheduler])
