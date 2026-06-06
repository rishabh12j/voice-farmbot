#!/bin/bash
# Per-session helper to bring up the Rishabh_Growmate_FarmBot overlay
# WITHOUT modifying ~/.bashrc and WITHOUT polluting the standalone
# ~/FarmBot_ROS2 install (so other people working on standalone aren't
# disturbed).
#
# Why this exists:
#   - .bashrc on the Maynooth Pis auto-sources ~/FarmBot_ROS2/install/
#     setup.bash on every new shell — useful for the upstream user, bad
#     for us because Rishabh's setup.bash chain-sources FarmBot_ROS2
#     internally and FarmBot_ROS2 ends up at the FRONT of
#     AMENT_PREFIX_PATH after Rishabh is sourced. Result: every ros2
#     pkg prefix query returns the standalone copy instead of ours.
#
# What this does:
#   1. Sources Rishabh's overlay normally.
#   2. Reorders AMENT_PREFIX_PATH so all /Rishabh_Growmate_FarmBot/
#      entries come first, all other entries (FarmBot_ROS2 included)
#      come after. So Rishabh wins where it has packages; FarmBot_ROS2
#      still supplies anything Rishabh doesn't (hardware_communication,
#      farmbot_hri, etc.).
#   3. Adds Rishabh's src/ to PYTHONPATH so growmate_pi imports cleanly.
#   4. Prints the AMENT_PREFIX_PATH head + a for-loop showing which
#      workspace each FarmBot package now resolves to — so you can
#      verify BEFORE running the section 1.4 map copy (which writes
#      into whichever install map_handler points at — get that wrong
#      and you overwrite the other person's active_map.yaml).
#
# Usage:
#   source ~/use-rishabh.sh        # MUST be `source`, not `bash`
#
# To install on a new Pi:
#   cp ~/Rishabh_Growmate_FarmBot/tools/use-rishabh.sh ~/use-rishabh.sh
#   chmod +x ~/use-rishabh.sh

# Source Rishabh's overlay. Chain-sources its own dependencies (which
# may put FarmBot_ROS2 in front of us); we fix the order below.
source ~/Rishabh_Growmate_FarmBot/install/setup.bash

# Move Rishabh-install entries to the front of AMENT_PREFIX_PATH;
# leave FarmBot_ROS2 and everything else after. So overlay packages
# win where they exist; fallback for the rest.
_rishabh_paths=$(echo "$AMENT_PREFIX_PATH" | tr ':' '\n' | grep '/Rishabh_Growmate_FarmBot/' | paste -sd ':')
_other_paths=$(echo "$AMENT_PREFIX_PATH" | tr ':' '\n' | grep -v '/Rishabh_Growmate_FarmBot/' | paste -sd ':')
if [ -n "$_rishabh_paths" ] && [ -n "$_other_paths" ]; then
    export AMENT_PREFIX_PATH="${_rishabh_paths}:${_other_paths}"
elif [ -n "$_rishabh_paths" ]; then
    export AMENT_PREFIX_PATH="$_rishabh_paths"
fi
unset _rishabh_paths _other_paths

# Same for PYTHONPATH so `python -m growmate_pi.intent_server` imports
# from Rishabh's src/ rather than wherever else.
case ":${PYTHONPATH:-}:" in
    *":$HOME/Rishabh_Growmate_FarmBot/src:"*) : ;;   # already there, no-op
    *) export PYTHONPATH="$HOME/Rishabh_Growmate_FarmBot/src${PYTHONPATH:+:$PYTHONPATH}" ;;
esac

# Verify
echo "=== Rishabh overlay active (this shell only) ==="
echo "AMENT_PREFIX_PATH head:"
echo "$AMENT_PREFIX_PATH" | tr ':' '\n' | head -8 | sed 's/^/  /'
echo "---"
echo "Package resolutions:"
for pkg in farmbot_bringup farmbot_controllers farmbot_command_handler farmbot_interfaces map_handler hardware_communication farmbot_hri; do
    prefix=$(ros2 pkg prefix $pkg 2>/dev/null || echo NOT-FOUND)
    # tag with a short marker so it's obvious where each one is coming from
    case "$prefix" in
        */Rishabh_Growmate_FarmBot/install/*) tag="[rishabh]" ;;
        */FarmBot_ROS2/install/*)              tag="[standalone]" ;;
        NOT-FOUND)                             tag="[missing]" ;;
        *)                                     tag="[other]" ;;
    esac
    printf "  %-30s %-13s %s\n" "$pkg" "$tag" "$prefix"
done
echo "---"
echo "Safe to run section 1.4 (map copy) ONLY if map_handler shows [rishabh] above."
