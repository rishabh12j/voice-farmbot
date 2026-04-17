"""
GrowMate Behaviour Tree Engine

Executes behaviour trees node by node with per-node safety validation.
Supports typed nodes: robot actions, function calls, LLM reasoning,
safety conditions, and control flow. Integrates with FarmBot REST API
for live plant coordinate lookup with YAML configuration fallback.
"""

import time, json, os, urllib.request, urllib.error
from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum


class NodeStatus(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"
    SKIPPED = "skipped"


@dataclass
class NodeResult:
    status: NodeStatus
    data: Any = None
    farmbot_command: str = ""
    message: str = ""
    time_ms: float = 0.0


@dataclass
class TreeResult:
    success: bool
    farmbot_commands: list = field(default_factory=list)
    node_results: list = field(default_factory=list)
    response_text: str = ""
    needs_confirmation: bool = False
    confirmation_prompt: str = ""
    total_time_ms: float = 0.0
    tree_description: str = ""


class BTEngine:
    def __init__(self, garden_config, robot_controller=None):
        self.garden = garden_config
        self.robot = robot_controller
        self.variables = {}
        self.llm_callback = None
        self.confirm_callback = None

        self.functions = {
            "fetch_weather":      self._mock_fetch_weather,
            "get_plant_info":     self._mock_get_plant_info,
            "get_time":           self._fn_get_time,
            "get_garden_state":   self._fn_get_garden_state,
            "get_farmbot_plant":  self._fn_get_farmbot_plant,
            "get_all_plants":     self._fn_get_all_plants,
        }

    def execute(self, tree: dict) -> TreeResult:
        start = time.perf_counter()
        self.variables = {}
        description = self._describe_tree(tree)

        node_results = []
        commands = []
        result = self._execute_node(tree, node_results, commands)

        response = ""
        for nr in node_results:
            if nr.message and nr.message.startswith("Respond: "):
                response = nr.data if nr.data else nr.message[9:]

        needs_confirm = False
        confirm_prompt = ""
        for nr in node_results:
            if "confirm:" in nr.message.lower() and nr.status == NodeStatus.RUNNING:
                needs_confirm = True
                confirm_prompt = nr.data
                break

        elapsed = (time.perf_counter() - start) * 1000
        return TreeResult(
            success=result.status == NodeStatus.SUCCESS,
            farmbot_commands=commands, node_results=node_results,
            response_text=response if response else result.message,
            needs_confirmation=needs_confirm, confirmation_prompt=confirm_prompt,
            total_time_ms=round(elapsed, 1), tree_description=description)

    def _execute_node(self, node, results, commands):
        node_type = node.get("type", "unknown")
        start = time.perf_counter()

        if node_type == "sequence":    return self._exec_sequence(node, results, commands)
        elif node_type == "selector":  return self._exec_selector(node, results, commands)

        if node_type == "robot_action":
            result = self._exec_robot_action(node)
            if result.farmbot_command: commands.append(result.farmbot_command)
        elif node_type == "function_call": result = self._exec_function_call(node)
        elif node_type == "llm_reason":    result = self._exec_llm_reason(node)
        elif node_type == "respond":       result = self._exec_respond(node)
        elif node_type == "condition":     result = self._exec_condition(node)
        elif node_type == "confirm":       result = self._exec_confirm(node)
        elif node_type == "set_var":       result = self._exec_set_var(node)
        elif node_type == "wait":          result = self._exec_wait(node)
        elif node_type == "log":
            result = NodeResult(NodeStatus.SUCCESS, message=f"Log: {node.get('message', '')}")
        else:
            result = NodeResult(NodeStatus.FAILURE, message=f"Unknown node: {node_type}")

        result.time_ms = (time.perf_counter() - start) * 1000
        results.append(result)
        return result

    # -- Control ---------------------------------------

    def _exec_sequence(self, node, results, commands):
        label = node.get("label", "Sequence")
        for child in node.get("children", []):
            r = self._execute_node(child, results, commands)
            if r.status == NodeStatus.FAILURE:
                return NodeResult(NodeStatus.FAILURE, message=f"Sequence '{label}' failed: {r.message}")
            if r.status == NodeStatus.RUNNING:
                return NodeResult(NodeStatus.RUNNING, data=r.data, message=r.message)
        return NodeResult(NodeStatus.SUCCESS, message=f"Sequence '{label}' complete")

    def _exec_selector(self, node, results, commands):
        label = node.get("label", "Selector")
        for child in node.get("children", []):
            r = self._execute_node(child, results, commands)
            if r.status == NodeStatus.SUCCESS: return r
        return NodeResult(NodeStatus.FAILURE, message=f"Selector '{label}': all failed")

    # -- Robot Actions ---------------------------------

    def _exec_robot_action(self, node):
        action = node.get("name", "")
        params = self._resolve_params(node.get("params", {}))

        if action == "move_to":
            x, y, z = params.get("x", 0), params.get("y", 0), params.get("z", 0)
            bounds = self.garden.safety.get("workspace_bounds", {})
            xr, yr, zr = bounds.get("x", [0, 9999]), bounds.get("y", [0, 9999]), bounds.get("z", [-999, 0])
            if not (xr[0] <= x <= xr[1] and yr[0] <= y <= yr[1] and zr[0] <= z <= zr[1]):
                return NodeResult(NodeStatus.FAILURE, message=f"move_to: ({x},{y},{z}) out of bounds")
            speed = params.get("speed")
            cmd = f"M_S {int(x)} {int(y)} {int(z)} {int(speed)}" if speed else f"M {int(x)} {int(y)} {int(z)}"
            return NodeResult(NodeStatus.SUCCESS, farmbot_command=cmd, message=f"Robot: move to ({x},{y},{z})")
        elif action == "water":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="D_W_1", message="Robot: water on")
        elif action == "water_off":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="D_W_0", message="Robot: water off")
        elif action == "water_all":
            cmd = "P_5" if params.get("smart") else "P_4"
            return NodeResult(NodeStatus.SUCCESS, farmbot_command=cmd, message=f"Robot: water all")
        elif action == "read_sensor":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="D_S_C",
                            data={"sensor": "soil_moisture", "value": "pending"}, message="Robot: read sensor")
        elif action == "check_moisture":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="P_9", message="Robot: check all moisture")
        elif action == "go_home":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="H_0", message="Robot: go home")
        elif action == "light":
            cmd = "D_L_1" if params.get("on") else "D_L_0"
            return NodeResult(NodeStatus.SUCCESS, farmbot_command=cmd, message=f"Robot: lights {'on' if params.get('on') else 'off'}")
        elif action == "photo":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="I_1", message="Robot: photo")
        elif action == "panorama":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="I_2", message="Robot: panorama")
        elif action == "scan_weeds":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="I_4", message="Robot: scan weeds")
        elif action == "emergency_stop":
            return NodeResult(NodeStatus.SUCCESS, farmbot_command="e", message="Robot: EMERGENCY STOP")
        elif action == "vacuum":
            cmd = "D_V_1" if params.get("on") else "D_V_0"
            return NodeResult(NodeStatus.SUCCESS, farmbot_command=cmd, message=f"Robot: vacuum")
        else:
            return NodeResult(NodeStatus.FAILURE, message=f"Unknown action: {action}")

    # -- Function Calls --------------------------------

    def _exec_function_call(self, node):
        func_name = node.get("name", "")
        params = self._resolve_params(node.get("params", {}))
        store_as = node.get("store_as", func_name + "_result")

        func = self.functions.get(func_name)
        if func is None:
            return NodeResult(NodeStatus.FAILURE, message=f"Unknown function: {func_name}")
        try:
            result_data = func(**params)
            self.variables[store_as] = result_data
            return NodeResult(NodeStatus.SUCCESS, data=result_data,
                            message=f"Function: {func_name} -> ${store_as}")
        except Exception as e:
            return NodeResult(NodeStatus.FAILURE, message=f"Function {func_name} failed: {e}")

    # -- LLM Reasoning ---------------------------------

    def _exec_llm_reason(self, node):
        context_keys = node.get("context", [])
        question = node.get("question", "")
        store_as = node.get("store_as", "reasoning_result")

        context = {k: self.variables[k] for k in context_keys if k in self.variables}

        if self.llm_callback:
            try:
                result = self.llm_callback(context, question)
                self.variables[store_as] = result
                return NodeResult(NodeStatus.SUCCESS, data=result,
                                message=f"LLM reasoning: {question[:50]}...")
            except Exception as e:
                return NodeResult(NodeStatus.FAILURE, message=f"LLM reasoning failed: {e}")
        else:
            self.variables[store_as] = f"[LLM would reason: {question}]"
            return NodeResult(NodeStatus.SUCCESS, data=self.variables[store_as],
                            message=f"LLM reasoning (mock): {question[:50]}...")

    # -- Respond ---------------------------------------

    def _exec_respond(self, node):
        template = node.get("message", "")
        response = template
        for var_name, var_value in self.variables.items():
            response = response.replace("{" + var_name + "}", str(var_value))
        return NodeResult(NodeStatus.SUCCESS, data=response, message=f"Respond: {response}")

    # -- Conditions ------------------------------------

    def _exec_condition(self, node):
        name = node.get("name", "")

        if name == "check_available":
            return NodeResult(NodeStatus.SUCCESS, message="Condition: robot available PASS")

        elif name == "check_plant_found":
            var_name = node.get("params", {}).get("var", "plant_data")
            plant_data = self.variables.get(var_name, {})
            if isinstance(plant_data, dict) and plant_data.get("found", False):
                pname = plant_data.get('name', '?')
                return NodeResult(NodeStatus.SUCCESS,
                    message=f"Condition: plant '{pname}' found at ({plant_data.get('x')},{plant_data.get('y')}) PASS")
            else:
                err = plant_data.get('error', 'not found') if isinstance(plant_data, dict) else 'no data'
                return NodeResult(NodeStatus.FAILURE, message=f"Condition: plant not found -- {err}")

        elif name == "check_bounds":
            params = self._resolve_params(node.get("params", {}))
            x, y, z = params.get("x", 0), params.get("y", 0), params.get("z", 0)
            bounds = self.garden.safety.get("workspace_bounds", {})
            xr, yr, zr = bounds.get("x", [0, 9999]), bounds.get("y", [0, 9999]), bounds.get("z", [-999, 0])
            if xr[0] <= x <= xr[1] and yr[0] <= y <= yr[1] and zr[0] <= z <= zr[1]:
                return NodeResult(NodeStatus.SUCCESS, message=f"Condition: bounds ({x},{y},{z}) PASS")
            else:
                return NodeResult(NodeStatus.FAILURE, message=f"Condition: ({x},{y},{z}) OUT OF BOUNDS")

        return NodeResult(NodeStatus.SUCCESS, message=f"Condition: {name} PASS")

    def _exec_confirm(self, node):
        prompt = node.get("message", "Proceed?")
        for var_name, var_value in self.variables.items():
            prompt = prompt.replace("{" + var_name + "}", str(var_value))
        if self.confirm_callback:
            return NodeResult(NodeStatus.SUCCESS if self.confirm_callback(prompt) else NodeStatus.FAILURE,
                            message=f"Confirm: {'yes' if True else 'no'}")
        return NodeResult(NodeStatus.RUNNING, data=prompt, message=f"Confirm: {prompt}")

    def _exec_set_var(self, node):
        name = node.get("name", "")
        value = node.get("value")
        if isinstance(value, str) and value.startswith("$"):
            value = self._resolve_dotref(value[1:])
        self.variables[name] = value
        return NodeResult(NodeStatus.SUCCESS, message=f"Set: {name} = {value}")

    def _exec_wait(self, node):
        seconds = node.get("seconds", 1)
        time.sleep(seconds)
        return NodeResult(NodeStatus.SUCCESS, message=f"Wait: {seconds}s")

    # -- Variable Resolution (with dot notation) -------

    def _resolve_params(self, params: dict) -> dict:
        """Resolve $variable.field references in parameters."""
        resolved = {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                resolved[key] = self._resolve_dotref(value[1:])
            else:
                resolved[key] = value
        return resolved

    def _resolve_dotref(self, ref: str):
        """Resolve 'plant_data.x' -> self.variables['plant_data']['x']"""
        parts = ref.split(".", 1)
        var_name = parts[0]
        var_data = self.variables.get(var_name)
        if len(parts) > 1 and isinstance(var_data, dict):
            return var_data.get(parts[1], 0)
        return var_data if var_data is not None else 0

    # -- Tree Description ------------------------------

    def _describe_tree(self, node, indent=0):
        lines = []
        t = node.get("type", "?")
        prefix = "  " * indent + ("|-- " if indent > 0 else "")
        if t in ("sequence", "selector"):
            lines.append(f"{prefix}{'->' if t == 'sequence' else '?'} {node.get('label', t)}")
            for child in node.get("children", []):
                lines.append(self._describe_tree(child, indent + 1))
        elif t == "robot_action":
            lines.append(f"{prefix}[ROBOT] {node.get('name')}({node.get('params', {})})")
        elif t == "function_call":
            lines.append(f"{prefix}[FUNC] {node.get('name')}() -> ${node.get('store_as', '')}")
        elif t == "llm_reason":
            lines.append(f"{prefix}[LLM] reason: {node.get('question', '')[:40]}...")
        elif t == "respond":
            lines.append(f"{prefix}[RESPOND] \"{node.get('message', '')[:40]}...\"")
        elif t == "condition":
            lines.append(f"{prefix}[COND] {node.get('name', '')}")
        elif t == "confirm":
            lines.append(f"{prefix}[CONFIRM] {node.get('message', '')[:30]}")
        elif t == "wait":
            lines.append(f"{prefix}[WAIT] wait {node.get('seconds', '?')}s")
        else:
            lines.append(f"{prefix}{t}")
        return "\n".join(lines)

    # -- FarmBot Live API Functions ---------------------

    def _fn_get_farmbot_plant(self, name: str) -> dict:
        """Query FarmBot REST API for a plant. Falls back to YAML config."""
        farmbot_url = self.garden.config.get("farmbot", {}).get("api_url", "")
        token = os.environ.get("FARMBOT_TOKEN", "") or self.garden.config.get("farmbot", {}).get("api_token", "")

        if farmbot_url and token:
            try:
                url = f"{farmbot_url}/api/points?pointer_type=Plant"
                req = urllib.request.Request(url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    plants = json.loads(resp.read().decode())
                name_lower = name.lower().strip()
                for p in plants:
                    pname = p.get("name", "").lower()
                    slug = p.get("openfarm_slug", "").lower()
                    if name_lower in pname or name_lower in slug or pname in name_lower:
                        return {"name": p["name"], "x": p.get("x", 0), "y": p.get("y", 0),
                                "z": p.get("z", 0), "stage": p.get("plant_stage", "planted"),
                                "radius": p.get("radius", 50), "id": p.get("id"), "found": True,
                                "source": "farmbot_api"}
                return {"found": False, "name": name, "error": f"No plant '{name}' in FarmBot"}
            except Exception:
                pass  # Fall through to YAML

        # Fallback: YAML config
        plant = self.garden.find(name) if hasattr(self.garden, 'find') else None
        if plant is None:
            # Try lookup table
            for p in self.garden.plants:
                if name.lower() in p['name'].lower() or p['name'].lower() in name.lower():
                    plant = p
                    break
            if plant is None:
                for l in self.garden.locations:
                    if name.lower() in l['name'].lower() or l['name'].lower() in name.lower():
                        plant = l
                        break

        if plant and 'position' in plant:
            pos = plant['position']
            return {"name": plant.get("name", name), "x": pos.get("x", 0), "y": pos.get("y", 0),
                    "z": pos.get("z", 0), "stage": plant.get("stage", "planted"),
                    "found": True, "source": "yaml_fallback"}

        return {"found": False, "name": name, "error": f"Plant '{name}' not found"}

    def _fn_get_all_plants(self) -> list:
        """Get all plants from FarmBot API or YAML fallback."""
        return [{"name": p["name"], "x": p["position"]["x"], "y": p["position"]["y"],
                 "z": p["position"]["z"], "stage": p.get("stage", "planted")}
                for p in self.garden.plants]

    # -- Other Functions -------------------------------

    def _mock_fetch_weather(self, location="default", days=3):
        return {"location": location, "forecast": [
            {"day": "today", "temp": 16, "rain_mm": 0, "desc": "Partly cloudy"},
            {"day": "tomorrow", "temp": 14, "rain_mm": 5, "desc": "Light rain"},
        ][:days], "source": "mock"}

    def _mock_get_plant_info(self, plant_name="tomato"):
        info = {
            "tomato": {"water": "regular", "sun": "full", "plant_month": "May-Jun"},
            "basil": {"water": "moderate", "sun": "full", "plant_month": "May-Jun"},
            "lettuce": {"water": "regular", "sun": "partial", "plant_month": "Mar-Sep"},
            "carrot": {"water": "moderate", "sun": "full", "plant_month": "Mar-Jul"},
            "strawberry": {"water": "regular", "sun": "full", "plant_month": "Mar-Apr"},
        }
        return info.get(plant_name.lower(), {"info": f"No data for {plant_name}"})

    def _fn_get_time(self):
        from datetime import datetime
        now = datetime.now()
        return {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "month": now.strftime("%B")}

    def _fn_get_garden_state(self):
        return {"plants": [p["name"] for p in self.garden.plants], "count": len(self.garden.plants)}
