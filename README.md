# GrowMate fork — voice control for FarmBot ROS2

This is a fork of the [AURA FarmBot ROS2 stack](https://github.com/PetriJF/FarmBot_ROS2)
with one additional package: **`src/growmate_voice`**, a drop-in voice
controller that replaces `keyboard_controller` with natural-language
speech input processed through an on-device LLM and an inspectable
behaviour tree pipeline. Everything in the upstream repository is
unchanged. The original README is reproduced below.

**What is different about this fork:**

- `src/growmate_voice/` — new ROS2 ament-python package. Publishes to
  the same `keyboard_topic` the existing `keyboard_controller` uses,
  so nothing downstream of the topic needed to change.
- Three entry points: a **Gradio web app** with browser mic, plan
  preview, and emergency stop button (`voice_app`); a **headless
  terminal node** that is a true drop-in for `keyboard_controller`
  (`voice_controller`); and a **desktop CLI** that runs the same
  pipeline in simulation mode for prompt iteration and evaluation
  (`voice_cli`).
- An on-device LLM (Gemma 3 4B via Ollama by default) classifies intent
  from natural speech; deterministic Python code assembles a behaviour
  tree from a typed node library; a BT engine executes the tree with
  per-node safety validation before any command reaches the robot.

**Quick start:**

```bash
# Build
colcon build --packages-select growmate_voice
source install/setup.bash

# Terminal 1 — standard FarmBot bringup (unchanged)
ros2 launch farmbot_bringup standard.launch.py

# Terminal 2 — voice app (browser mic) or headless terminal node
ros2 run growmate_voice voice_app          # web UI on port 7860
# or
ros2 run growmate_voice voice_controller   # drop-in for keyboard_controller
```

Full installation instructions, prerequisites (Ollama, faster-whisper,
TTS backends), deployment topology notes (Pi-resident vs separate
machine), and troubleshooting are in
**[`src/growmate_voice/README.md`](src/growmate_voice/README.md)**.

This fork is part of the MSc thesis *"GrowMate: Transparent Voice-Robot
Interaction through LLM-Constructed Behaviour Trees for Accessible
Agricultural Robotics,"* Maynooth University, 2026.

---

# Description

[![ROS2Humble](https://img.shields.io/badge/ROS2_Humble-Ubuntu_22.04-blue.svg)](https://docs.ros.org/en/humble/index.html)
[![ROS2Jazzy](https://img.shields.io/badge/ROS2_Jazzy-Ubuntu_24.04-green.svg)](https://docs.python.org/3/whatsnew/3.10.html)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](https://opensource.org/license/MIT/)

This repository contains a ROS2 alternative to the Farmbot OS, which was originally written in Elixir, Lua and Python. This repository assumes you are using an unmodified version of the [Original Farmduino Firmware](https://github.com/FarmBot/farmbot-arduino-firmware). Note that there is no option within the repository yet for loading the firmware on the Farmduino (You can load it by running the original RPi software at least once with the system).

The codebase was tested on both ROS2 Humble (Ubuntu 22.04 - RPi 4&5) and ROS2 Jazzy (Ubuntu 24.04 - RPi 5). When preparing the environment, install the full version (not the barebones).

Our implementation works purely from the terminal. You will need to SSH into the RPi and run 2 or 3 terminals (with the corrent ROS_DOMAIN_ID if running multiple farmbots from the same control machine). There are plans to add a user friendly GUI, but this task is currently in the backlog with low priority.

# Disclaimer

Please note that this is not a 1:1 refactoring of Farmbot OS. It contains all the tools necessary to run and utilize the main features of the Farmbots. The software was tested on the Genesis XL platform (v1.6 and v1.7), but it should work on the Express platform (needs testing).

This repository was developed in the AURA Project at Maynooth University and is a public fork of the research repository. Some features are specific to the research taking place in the project, and missing features will be added based on the priority dictated by the tasks and projects going on at the time.

# Preparing the Environment

### 1. Clone the respository

Execute the following to clone the repo as a new subdirectory `/home/<yourname>/` containing the Farmbot ROS2 code base.
``` bash
git clone https://github.com/PetriJF/FarmBot_ROS2.git
```

(OPTIONAL) And enable the submodules you might want to use.
``` bash
git submodule init src/X    # replace X with the package name
git submodule update src/X  # replace X with the package name
```

Alternatively, clone with the submodules (Optional -- these submodules represent experimental features done in the AURA team. Some submodules might be private and cannot be accessed until fully released)
``` bash
git clone --recurse-submodules git@github.com:PetriJF/FarmBot_ROS2.git
```
### 2. Set your bashrc (Optional)

Open the bashrc file in your user's directory

``` bash
cd ~
ls -a
nano .bashrc
```

Once open your terminal should look like a command editor. Go to the very end and add the following lines there:

``` bash
# ROS2 sourcing
source /opt/ros/humble/setup.bash          # Replace humble with jazzy if that's the case
source ~/FarmBot_ROS2/install/setup.bash
```

Note, if you installed something in a different directory than the default ones, you will have to adapt the 3 sourcing commands

### 3. Install required packages

``` bash
sudo apt update
sudo apt upgrade
```

**Install these if you are running on the Raspberry Pi 4 and ROS2 Humble (Assuming ROS2 Humble already installed)**
```bash

```
**Install these if you are running on the Raspberry Pi 5 and ROS2 Jazzy (Assuming ROS2 Jazzy already installed)**
``` bash
sudo apt install python3-pip
sudo apt install python3-rpi-lgpio          # Compatible GPIO Library
## MCPC
pip install depthai --break-system-packages # To pass by PEP 668 (externally-managed-environment)
# Run these with the Luxonis Cameras unplugged
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```


### 4. Test the workspace

Attempt to run the farmbot launch file, starting up all the nodes needed for it to run

``` bash
ros2 launch farmbot_bringup standard.launch.py
```

You can also run the keyboard controller in another terminal to test a command

``` bash
ros2 run farmbot_controllers keyboard_controller
```

You can now check the High Level Controller Commands list to test the various functions of the Farmbot.

# How to run everything together (WIP - Subject to change)

Before anything else, ensure that you have the most recent commit, and you properly built the workspace.

You will need a minimum of 2 terminals for each robot (3 if you want manual user control). All three terminals are explained below:

### 1. The Farmbot Packages

These packages include everything from the main controller to the imagine controller. This will run all the nodes needed to interpret and form the commands that communicate with the Farmduino. Also, imaging and plant management happen here as well.

For running the launch file use:
``` bash
ros2 launch farmbot_bringup standard.launch.py
```

### 2. Launch the Autonomous Command Controller

This is a node that sends commands to the farmbot controller at specific times set in the script. If you want to create a specific plan for watering or managing your plants, make sure to modify the python script to suit your needs.

``` bash
ros2 run farmbot_controllers keyboard_controller
```

### 3. User Command Handler (Optional)

If you want to send commands to the farmbot that are outside the plan you have created for your plants, you can turn on the keyboard controller. For a complete list of all the High Level commands, check the documentation.

``` bash
ros2 run farmbot_controllers autonomous_controller
```
