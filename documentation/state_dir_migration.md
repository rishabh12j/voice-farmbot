# Single writable state dir (Option 2) — design + migration

**Date:** 2026-07-08. Status: **map core done** (active_map + watering_guide);
camera-calibration and firmware-param state are a documented follow-up.

## Why

Mutable robot state used to live in the colcon **build tree**
(`install/share/<pkg>/config`), mixed with read-only package data:

- `watering_guide.yaml` is shipped via `data_files`, so **every `colcon build`
  copied `src` → `install/share`, clobbering any runtime edit.**
- `active_map.yaml` is *not* in `data_files`, so it survived a normal rebuild —
  but a clean rebuild (`rm -rf install build`) wiped it, and GrowMate kept its
  own read path into `install/share`, so "the map" effectively had two homes.

Result: no single, durable, shared copy. Option 2 fixes this by moving mutable
state to **one writable directory outside the build tree**, read and written by
**both** the AURA stack and GrowMate.

## The state dir

`FARMBOT_STATE_DIR` (env), default **`~/.farmbot`**. Created on first run by
`map_handler.map_controller.farmbot_state_dir()`. The launch file
(`greenhouse.launch.py`) exports it to every node via a `state_dir:=` arg →
`SetEnvironmentVariable("FARMBOT_STATE_DIR", …)`, so map_controller, the camera
nodes, the intent server and the scheduler all agree.

### What moved vs. what stayed

| File | Role | Location now |
|---|---|---|
| `active_map.yaml` | live map (positions, tools, trays) | **state dir** (mutable) |
| `watering_guide.yaml` | AURA P_5/P_9 per-species pulse table | **state dir** (seeded from template on first run) |
| `plant_reference` / `tool_reference` / `tray_reference` / `16_seed_tray` / `map_references` | empty schema templates | package `share` (read-only) |
| `Genesis.yaml` / `firmwareDefault.yaml` … | firmware params | package `share` (read-only) — **not yet moved** |
| `activeConfig.yaml` | persisted firmware params | `install/share` — **not yet moved** (follow-up) |
| `camera_calibration.yaml`, `known_plants.yaml`, `other_plants.yaml` | vision state | `install/share` — **not yet moved** (follow-up) |

### Code touchpoints (map core)

- `map_handler/map_controller.py`: `farmbot_state_dir()` helper; split
  `template_dir_` (share, read-only) from `directory_` (state dir, mutable);
  `__seed_state_file` seeds `watering_guide` on first run; `retrieve_map` takes
  a `fallback_dir` for the fresh-run template.
- `camera_handler/panorama.py`, `plant_detection.py`: read `active_map` from the
  state dir (they were reading the old share copy).
- `growmate_pi/intent_server.py`: `_installed_map_path` checks the state dir
  first, then the legacy install-share, then the repo source (tests).
- `launch/greenhouse.launch.py`: `state_dir:=` arg → `FARMBOT_STATE_DIR`.

Backward-compatible: with no `~/.farmbot` present, GrowMate falls back to the
old install-share and then the repo source, so nothing breaks pre-migration.

## Migration — run once per Pi, BEFORE the first launch of this code

The existing live map is in `install/share`; copy it into the new state dir so
the robot keeps its garden. On each Pi:

```bash
STATE="${FARMBOT_STATE_DIR:-$HOME/.farmbot}"
mkdir -p "$STATE"
SHARE=$(ros2 pkg prefix map_handler)/share/map_handler/config
# 1. the live map (keeps your registered tools + plants)
cp -n "$SHARE/active_map.yaml" "$STATE/active_map.yaml" 2>/dev/null || \
  echo "no install-share active_map — will seed empty on first launch"
# 2. watering_guide (map_controller will also auto-seed it if absent)
cp -n "$SHARE/watering_guide.yaml" "$STATE/watering_guide.yaml" 2>/dev/null || true
ls -l "$STATE"
```

Then `git pull` + rebuild + launch as usual. Verify after launch:

```bash
# map_controller logs "Seeded watering_guide…" (first run) and reads the state dir
cat ~/.farmbot/active_map.yaml | grep plant_count      # your garden
curl -s http://localhost:8000/status | grep -o '"tool":[^,]*'   # GrowMate sees it too
```

If you ever want the old behaviour, launch with
`state_dir:=$(ros2 pkg prefix map_handler)/share/map_handler/config`.

## Follow-up (not done here)

Same relocation for the remaining runtime-generated state, deferred because it's
only robot-validatable and lower-stakes:

- `farmbot_controllers/config_managers.py` → `activeConfig.yaml` to the state dir.
- `camera_handler/calib.py`, `panorama.py`, `plant_detection.py` →
  `camera_calibration.yaml`, `known_plants.yaml`, `other_plants.yaml` to the
  state dir (GrowMate's `_camera_handler_config` would follow, like the map did).

## Validation

- `verify_sim` 0/12 (growmate_pi map-read path; falls back to repo source when
  no state dir exists).
- State-dir priority unit-checked: a state-dir `active_map` wins over
  install-share and source.
- The AURA node changes (map_controller, camera) are syntax-checked here but can
  only be fully validated on hardware — verify the migration steps above on the
  first real launch.
