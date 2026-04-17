"""Launch file for running GrowMate Voice alongside the FarmBot stack.

Two modes, selected by the ``mode`` launch argument:

* ``mode:=app`` (default) — brings up the Gradio web app on port 7860.
  Pair this with ``standard.launch.py`` from ``farmbot_bringup`` in a
  separate terminal, or use ``with_farmbot:=true`` to include it here.

* ``mode:=headless`` — runs the plain ``voice_controller`` node instead.
  This is the true drop-in for ``keyboard_controller``: a terminal prompt
  that publishes to ``keyboard_topic``. Useful on a display-less Pi.

Examples::

    # Voice app alone, assuming FarmBot is already running elsewhere
    ros2 launch growmate_voice growmate_voice.launch.py

    # Voice app + full FarmBot stack, one command
    ros2 launch growmate_voice growmate_voice.launch.py with_farmbot:=true

    # Headless text-only mode
    ros2 launch growmate_voice growmate_voice.launch.py mode:=headless

    # Pick a different Ollama model
    ros2 launch growmate_voice growmate_voice.launch.py model:=gemma3n:e2b
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    mode = LaunchConfiguration('mode')
    model = LaunchConfiguration('model')
    with_farmbot = LaunchConfiguration('with_farmbot')
    host = LaunchConfiguration('host')
    port = LaunchConfiguration('port')
    whisper = LaunchConfiguration('whisper')

    declare_mode = DeclareLaunchArgument(
        'mode', default_value='app',
        description="Run mode: 'app' for the Gradio web UI, 'headless' for the terminal node",
    )
    declare_model = DeclareLaunchArgument(
        'model', default_value='gemma3:4b',
        description='Ollama model name passed to the AI core',
    )
    declare_with_farmbot = DeclareLaunchArgument(
        'with_farmbot', default_value='false',
        description='Also bring up the full FarmBot stack (standard.launch.py)',
    )
    declare_host = DeclareLaunchArgument(
        'host', default_value='0.0.0.0',
        description='Gradio bind host (app mode only)',
    )
    declare_port = DeclareLaunchArgument(
        'port', default_value='7860',
        description='Gradio bind port (app mode only)',
    )
    declare_whisper = DeclareLaunchArgument(
        'whisper', default_value='tiny.en',
        description='faster-whisper model size (app mode only)',
    )

    # Optional: include the FarmBot bringup.
    farmbot_stack = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('farmbot_bringup'),
                'launch',
                'standard.launch.py',
            ])
        ]),
        condition=IfCondition(with_farmbot),
    )

    # App mode: Gradio web UI.
    voice_app = Node(
        package='growmate_voice',
        executable='voice_app',
        name='growmate_voice_app',
        output='screen',
        arguments=[
            '--model', model,
            '--host', host,
            '--port', port,
            '--whisper', whisper,
        ],
        condition=IfCondition(PythonExpression(["'", mode, "' == 'app'"])),
    )

    # Headless mode: plain voice_controller.
    voice_headless = Node(
        package='growmate_voice',
        executable='voice_controller',
        name='growmate_voice_controller',
        output='screen',
        arguments=['--model', model],
        condition=IfCondition(PythonExpression(["'", mode, "' == 'headless'"])),
    )

    return LaunchDescription([
        declare_mode,
        declare_model,
        declare_with_farmbot,
        declare_host,
        declare_port,
        declare_whisper,
        LogInfo(msg=['GrowMate Voice starting in mode: ', mode, ' (model=', model, ')']),
        farmbot_stack,
        voice_app,
        voice_headless,
    ])
