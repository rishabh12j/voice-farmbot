"""Launch file for the GrowMate FastAPI app.

Examples
--------
    # Voice + jog app on http://0.0.0.0:7860 (assumes FarmBot bringup is running)
    ros2 launch growmate_voice growmate_voice.launch.py

    # Bring up the full FarmBot stack alongside the app
    ros2 launch growmate_voice growmate_voice.launch.py with_farmbot:=true

    # Pick a different bind host / port
    ros2 launch growmate_voice growmate_voice.launch.py host:=127.0.0.1 port:=8080
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    with_farmbot = LaunchConfiguration('with_farmbot')
    host = LaunchConfiguration('host')
    port = LaunchConfiguration('port')

    declare_with_farmbot = DeclareLaunchArgument(
        'with_farmbot', default_value='false',
        description='Also bring up the full FarmBot stack (standard.launch.py)',
    )
    declare_host = DeclareLaunchArgument(
        'host', default_value='0.0.0.0',
        description='FastAPI bind host',
    )
    declare_port = DeclareLaunchArgument(
        'port', default_value='7860',
        description='FastAPI bind port',
    )

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

    voice_app = Node(
        package='growmate_voice',
        executable='voice_app',
        name='growmate_voice_app',
        output='screen',
        arguments=['--host', host, '--port', port],
    )

    return LaunchDescription([
        declare_with_farmbot,
        declare_host,
        declare_port,
        LogInfo(msg=['GrowMate Voice starting on ', host, ':', port]),
        farmbot_stack,
        voice_app,
    ])
