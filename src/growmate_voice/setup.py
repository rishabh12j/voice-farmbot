from setuptools import find_packages, setup
import os

package_name = 'growmate_voice'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'),
            [os.path.join('config', 'farmbot.yaml')]),
        (os.path.join('share', package_name, 'launch'),
            [os.path.join('launch', 'growmate_voice.launch.py')]),
    ],
    install_requires=['setuptools', 'pyyaml'],
    zip_safe=True,
    maintainer='Rishabh Jain',
    maintainer_email='rishabh.jain.2025@mumail.ie',
    description='Voice control for the AURA FarmBot ROS2 stack via LLM-constructed behaviour trees.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # Drop-in replacement for keyboard_controller. Headless, text input.
            'voice_controller = growmate_voice.voice_controller_node:main',
            # Same node, but with a Gradio web UI on port 7860.
            'voice_app = growmate_voice.app:main',
            # Pure CLI, no ROS2 (useful for desktop dev / paper evaluation).
            'voice_cli = growmate_voice.cli:main',
        ],
    },
)
