from setuptools import find_packages, setup
import os

package_name = 'growmate_voice'

setup(
    name=package_name,
    version='0.2.0',
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
    description='FastAPI voice + jog interface for the AURA FarmBot ROS2 stack.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # Main web app: gated jog + voice pipeline on http://localhost:7860
            'voice_app = growmate_voice.app:main',
            # Standalone voice-pipeline workbench on http://localhost:7870
            'voice_workbench = growmate_voice.stt_test:main',
            # Daily/recurring command scheduler — replaces autonomous_controller
            'voice_scheduler = growmate_voice.scheduler:main',
        ],
    },
)
