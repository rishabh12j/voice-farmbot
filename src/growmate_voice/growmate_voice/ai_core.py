"""
GrowMate AI Core

Hybrid intent classification and behaviour tree construction module.
The LLM classifies user intent from natural speech. Deterministic code
constructs structurally correct behaviour trees from a typed node library.
Supports multi-intent commands, FarmBot live API lookup, and mixed
robot-action and knowledge-query trees.
"""

import json, re, yaml, time, urllib.request, urllib.error
from typing import Optional, List

class GardenConfig:
    def __init__(self, config_path):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.robot = self.config.get('robot', {})
        self.plants = self.config.get('garden', {}).get('plants', [])
        self.locations = self.config.get('garden', {}).get('locations', [])
        self.commands = self.config.get('commands', {})
        self.safety = self.config.get('safety', {})
        loc = self.config.get('location', {})
        self.location_name = loc.get('name', 'default')
        self.location_country = loc.get('country', '')
        self.location_context = f"{self.location_name}, {self.location_country}" if self.location_country else self.location_name
        self.latitude = loc.get('latitude')
        self.longitude = loc.get('longitude')
        self._lookup = {}
        for p in self.plants:
            self._lookup[p['name'].lower()] = p
            for a in p.get('aliases', []): self._lookup[a.lower()] = p
        for l in self.locations:
            self._lookup[l['name'].lower()] = l
            for a in l.get('aliases', []): self._lookup[a.lower()] = l

    def find(self, name):
        if not name: return None
        name = name.lower().strip()
        if name in self._lookup: return self._lookup[name]
        for key, val in self._lookup.items():
            if name in key or key in name: return val
        return None

    def get_plant_list(self):
        lines = []
        for p in self.plants:
            pos = p['position']
            lines.append(f"- {p['name']}: x={pos['x']}, y={pos['y']}, z={pos['z']}")
        for l in self.locations:
            pos = l['position']
            lines.append(f"- {l['name']}: x={pos['x']}, y={pos['y']}, z={pos['z']}")
        return "\n".join(lines)

class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="gemma3:4b"):
        self.base_url, self.model = base_url, model

    def is_available(self):
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                models = [m.get('name', '') for m in data.get('models', [])]
                return any(self.model.split(':')[0] in m for m in models)
        except: return False

    def chat(self, system_prompt, user_message, temperature=0.2, max_tokens=400, json_mode=False):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens}
        }
        if json_mode: payload["format"] = "json"
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(f"{self.base_url}/api/chat", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
                return result.get('message', {}).get('content', '')
        except Exception as e:
            print(f"  Ollama error: {e}")
            return None

class AICore:
    ACTIONS = ["move", "water", "water_all", "water_smart", "go_home",
               "light_on", "light_off", "photo", "panorama", "scan_weeds",
               "clear_weeds", "scan_bed", "find_plants", "label_plants",
               "check_sensor", "check_moisture", "emergency_stop",
               "general_question"]

    def __init__(self, config_path, model="gemma3:4b", ollama_url="http://localhost:11434"):
        self.garden = GardenConfig(config_path)
        self.llm = OllamaClient(base_url=ollama_url, model=model)
        self.model = model

    def _classify_prompt(self, live_plants: Optional[List[str]] = None):
        # Ground the model in what is ACTUALLY planted (the live active_map from
        # the Pi) rather than a static config superset. This is the garden
        # "memory" the brain infers from: targets resolve to real plants, so we
        # stop mapping speech onto species that aren't in the bed.
        if live_plants:
            garden_section = (
                "CURRENTLY PLANTED (the plants actually in the garden right now "
                "— resolve plant targets to one of these names):\n  "
                + ", ".join(sorted({p for p in live_plants if p})))
        else:
            garden_section = ("GARDEN MAP (use these exact names for targets):\n"
                              + self.garden.get_plant_list())
        return f"""You are GrowMate, a voice assistant that controls a garden robot.
Your job is to classify what the user wants into a list of intents.

{garden_section}

AVAILABLE ACTIONS:
  ROBOT: move, water, water_all, water_smart, go_home, light_on, light_off, photo, panorama, scan_weeds, clear_weeds, scan_bed, find_plants, label_plants, check_sensor, check_moisture
  KNOWLEDGE: general_question

OUTPUT FORMAT -- always return JSON:
{{"intents": [{{"action": "<ACTION>", "target": "<plant/location or null>", "question": "<for general_question only, else null>", "response": "<short friendly reply>"}}]}}

RULES:
1. A single utterance may have MULTIPLE actions. Return ALL in order.
2. Robot + knowledge CAN mix: "water tomatoes and tell me about basil" = [water, general_question]
3. Indirect speech: "herbs look dry" = water herbs. "tomatoes look thirsty" = water tomatoes.
4. "how are the X" / "check on X" = check_sensor with target X
5. "is the soil moist" / "check moisture" (no specific plant) = check_moisture (no target)
6. "how is the soil at X" = check_sensor with target X
7. "X and then Y" or "X then Y" = two intents: [X, Y]
8. Each intent gets its own friendly response.
9. "water the DRY/thirsty ones", "only water what needs it", "water X if it's dry" = water_smart (reads soil per plant, waters only the dry ones). Plain "water the X" / "water everything" stays water / water_all.
10. "scan/look/check for weeds" = scan_weeds (detect only). "clear/pull/remove/get rid of the weeds", "do the weeding" = clear_weeds (physically removes detected weeds).
11. Map building flow: "scan the bed", "map the bed", "set up the map" = scan_bed (camera scans the bed to detect plants). Then "find the plants", "what did you find" = find_plants (stage detections for labelling). Then labelling replies like "the left bed is lettuce", "they're all tomatoes", "the middle are scallions" = label_plants, with target = the region+species phrase exactly ("left lettuce", "all tomatoes", "middle scallions"). Region words: left/middle/right/front/back/all.

EXAMPLES:
"water the tomatoes" -> {{"intents": [{{"action":"water","target":"tomatoes","question":null,"response":"Watering the tomatoes!"}}]}}
"move to the herbs" -> {{"intents": [{{"action":"move","target":"herbs","question":null,"response":"Moving to the herbs."}}]}}
"go home" -> {{"intents": [{{"action":"go_home","target":null,"question":null,"response":"Heading home."}}]}}
"the herbs seem dry" -> {{"intents": [{{"action":"water","target":"herbs","question":null,"response":"The herbs look thirsty, watering now."}}]}}
"how are the tomatoes looking" -> {{"intents": [{{"action":"check_sensor","target":"tomatoes","question":null,"response":"Let me check on the tomatoes."}}]}}
"is the soil moist enough" -> {{"intents": [{{"action":"check_moisture","target":null,"question":null,"response":"Checking soil moisture."}}]}}
"water everything" -> {{"intents": [{{"action":"water_all","target":null,"question":null,"response":"Watering all plants."}}]}}
"water the dry tomatoes" -> {{"intents": [{{"action":"water_smart","target":"tomatoes","question":null,"response":"Checking which tomatoes are dry and watering those."}}]}}
"only water the ones that need it" -> {{"intents": [{{"action":"water_smart","target":null,"question":null,"response":"Watering only the plants that are dry."}}]}}
"water the thirsty plants" -> {{"intents": [{{"action":"water_smart","target":null,"question":null,"response":"Watering the dry ones."}}]}}
"turn on lights" -> {{"intents": [{{"action":"light_on","target":null,"question":null,"response":"Lights on!"}}]}}
"turn off lights" -> {{"intents": [{{"action":"light_off","target":null,"question":null,"response":"Lights off."}}]}}
"take a photo" -> {{"intents": [{{"action":"photo","target":null,"question":null,"response":"Taking a photo."}}]}}
"scan for weeds" -> {{"intents": [{{"action":"scan_weeds","target":null,"question":null,"response":"Scanning for weeds."}}]}}
"clear the weeds" -> {{"intents": [{{"action":"clear_weeds","target":null,"question":null,"response":"Clearing the weeds."}}]}}
"pull the weeds in bed 2" -> {{"intents": [{{"action":"clear_weeds","target":"bed 2","question":null,"response":"Removing the weeds."}}]}}
"scan for weeds then clear them" -> {{"intents": [{{"action":"scan_weeds","target":null,"question":null,"response":"Scanning first."}},{{"action":"clear_weeds","target":null,"question":null,"response":"Now clearing them."}}]}}
"scan the bed" -> {{"intents": [{{"action":"scan_bed","target":null,"question":null,"response":"Scanning the bed for plants."}}]}}
"find the plants" -> {{"intents": [{{"action":"find_plants","target":null,"question":null,"response":"Let me find the plants."}}]}}
"the left bed is lettuce" -> {{"intents": [{{"action":"label_plants","target":"left lettuce","question":null,"response":"Labeling the left as lettuce."}}]}}
"they're all tomatoes" -> {{"intents": [{{"action":"label_plants","target":"all tomatoes","question":null,"response":"Labeling them all as tomatoes."}}]}}
"the middle are scallions" -> {{"intents": [{{"action":"label_plants","target":"middle scallions","question":null,"response":"Labeling the middle as scallions."}}]}}
"check moisture levels" -> {{"intents": [{{"action":"check_moisture","target":null,"question":null,"response":"Checking moisture."}}]}}
"when should I plant basil" -> {{"intents": [{{"action":"general_question","target":"basil","question":"When should I plant basil in {self.garden.location_context}?","response":"Let me look that up."}}]}}
"the tomatoes look thirsty" -> {{"intents": [{{"action":"water","target":"tomatoes","question":null,"response":"Tomatoes need water, on it!"}}]}}
"give the lettuce a drink" -> {{"intents": [{{"action":"water","target":"lettuce","question":null,"response":"Giving the lettuce some water."}}]}}
"take care of the strawberries" -> {{"intents": [{{"action":"water","target":"strawberries","question":null,"response":"Taking care of the strawberries."}}]}}
"the carrots need attention" -> {{"intents": [{{"action":"water","target":"carrots","question":null,"response":"Watering the carrots."}}]}}
"check on the lettuce for me" -> {{"intents": [{{"action":"check_sensor","target":"lettuce","question":null,"response":"Checking on the lettuce."}}]}}
"water the tomatoes and then go home" -> {{"intents": [{{"action":"water","target":"tomatoes","question":null,"response":"Watering tomatoes first."}},{{"action":"go_home","target":null,"question":null,"response":"Now heading home."}}]}}
"check on the herbs then water them" -> {{"intents": [{{"action":"check_sensor","target":"herbs","question":null,"response":"Checking the herbs."}},{{"action":"water","target":"herbs","question":null,"response":"Watering them now."}}]}}
"how often should I water tomatoes" -> {{"intents": [{{"action":"general_question","target":"tomatoes","question":"How often should I water tomatoes?","response":"Good question, let me check."}}]}}
"what vegetables grow well in spring" -> {{"intents": [{{"action":"general_question","target":null,"question":"What vegetables grow well in spring in {self.garden.location_context}?","response":"Let me find out."}}]}}
"water the tomatoes and tell me about basil" -> {{"intents": [{{"action":"water","target":"tomatoes","question":null,"response":"Watering the tomatoes."}},{{"action":"general_question","target":"basil","question":"Tell me about growing basil in {self.garden.location_context}.","response":"And here is some basil advice."}}]}}
"turn on the lights then take a photo" -> {{"intents": [{{"action":"light_on","target":null,"question":null,"response":"Lights on!"}},{{"action":"photo","target":null,"question":null,"response":"Taking a photo."}}]}}
"scan for weeds and check moisture" -> {{"intents": [{{"action":"scan_weeds","target":null,"question":null,"response":"Scanning for weeds."}},{{"action":"check_moisture","target":null,"question":null,"response":"Checking moisture too."}}]}}

Return JSON only. No explanations. Classify:"""

    # -- Classification --------------------------------

    def _classify(self, text, live_plants: Optional[List[str]] = None) -> Optional[List[dict]]:
        resp = self.llm.chat(self._classify_prompt(live_plants), text,
                             temperature=0.1, max_tokens=400, json_mode=True)
        if not resp: return None
        parsed = self._parse_json_full(resp)
        if not parsed: return None
        # New format: {"intents": [...]}
        if "intents" in parsed and isinstance(parsed["intents"], list):
            return parsed["intents"]
        # Old format fallback: {"action": ..., "target": ..., "response": ...}
        if "action" in parsed:
            return [parsed]
        return None

    # -- Tree Construction -----------------------------

    def construct_tree(self, user_input):
        if self._is_emergency(user_input):
            return self._tree_emergency()

        intents = self._classify(user_input)
        if not intents:
            return self._tree_respond("I didn't understand that. Could you try again?")

        # Single intent
        if len(intents) == 1:
            return self._intent_to_tree(intents[0])

        # Multiple intents -> flatten into one sequence
        all_children = []
        labels = []
        for intent in intents:
            subtree = self._intent_to_tree(intent)
            if subtree is None: continue
            if subtree.get("type") == "sequence":
                all_children.extend(subtree.get("children", []))
                labels.append(subtree.get("label", intent.get("action", "step")))
            else:
                all_children.append(subtree)
                labels.append(intent.get("action", "step"))

        if not all_children:
            return self._tree_respond("I couldn't process that request.")

        return {"type": "sequence", "label": " then ".join(labels), "children": all_children}

    def _intent_to_tree(self, intent):
        action = intent.get("action", "general_question")
        target = intent.get("target")
        question = intent.get("question") or ""
        resp = intent.get("response", "Done!")

        if action == "move":            return self._tree_move(target, resp)
        elif action == "water":         return self._tree_water(target, resp)
        elif action == "water_all":     return self._tree_water_all(resp)
        elif action == "go_home":       return self._tree_simple("go_home", {}, resp)
        elif action == "light_on":      return self._tree_simple("light", {"on": True}, resp)
        elif action == "light_off":     return self._tree_simple("light", {"on": False}, resp)
        elif action == "photo":         return self._tree_simple("photo", {}, resp)
        elif action == "panorama":      return self._tree_simple("panorama", {}, resp)
        elif action == "scan_weeds":    return self._tree_simple("scan_weeds", {}, resp)
        elif action == "check_sensor":  return self._tree_sensor(target, resp)
        elif action == "check_moisture":return self._tree_simple("check_moisture", {}, resp)
        elif action == "emergency_stop":return self._tree_emergency()
        elif action == "general_question": return self._tree_general(question or target or "garden advice", resp)
        else: return self._tree_respond(resp)

    # -- Tree Builders (with FarmBot live lookup) ------

    def _tree_move(self, target, resp):
        """Move: query FarmBot -> check exists -> check bounds -> move -> respond"""
        return {"type": "sequence", "label": f"Move to {target}", "children": [
            {"type": "condition", "name": "check_available"},
            {"type": "function_call", "name": "get_farmbot_plant",
             "params": {"name": target or "unknown"}, "store_as": "plant_data"},
            {"type": "condition", "name": "check_plant_found", "params": {"var": "plant_data"}},
            {"type": "condition", "name": "check_bounds",
             "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}},
            {"type": "robot_action", "name": "move_to",
             "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}},
            {"type": "respond", "message": resp}
        ]}

    def _tree_water(self, target, resp):
        """Water: query FarmBot -> check -> move -> water on -> wait -> water off -> respond"""
        plant = self.garden.find(target)
        dur_s = plant.get('water_quantity', 6) if plant else 6
        return {"type": "sequence", "label": f"Water {target}", "children": [
            {"type": "condition", "name": "check_available"},
            {"type": "function_call", "name": "get_farmbot_plant",
             "params": {"name": target or "unknown"}, "store_as": "plant_data"},
            {"type": "condition", "name": "check_plant_found", "params": {"var": "plant_data"}},
            {"type": "condition", "name": "check_bounds",
             "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}},
            {"type": "robot_action", "name": "move_to",
             "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}},
            {"type": "robot_action", "name": "water", "params": {"duration": dur_s * 1000}},
            {"type": "wait", "seconds": dur_s},
            {"type": "robot_action", "name": "water_off", "params": {}},
            {"type": "respond", "message": resp}
        ]}

    def _tree_water_all(self, resp):
        return {"type": "sequence", "label": "Water all", "children": [
            {"type": "condition", "name": "check_available"},
            {"type": "confirm", "message": "Water all plants in the garden. Go ahead?"},
            {"type": "robot_action", "name": "water_all", "params": {"smart": False}},
            {"type": "respond", "message": resp}
        ]}

    def _tree_sensor(self, target, resp):
        """Sensor check: query FarmBot -> move -> read -> reason -> respond"""
        children = [{"type": "condition", "name": "check_available"}]
        if target:
            children.append({"type": "function_call", "name": "get_farmbot_plant",
                           "params": {"name": target}, "store_as": "plant_data"})
            children.append({"type": "condition", "name": "check_plant_found",
                           "params": {"var": "plant_data"}})
            children.append({"type": "condition", "name": "check_bounds",
                           "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}})
            children.append({"type": "robot_action", "name": "move_to",
                           "params": {"x": "$plant_data.x", "y": "$plant_data.y", "z": "$plant_data.z"}})
        children.append({"type": "robot_action", "name": "read_sensor", "params": {}})
        children.append({"type": "llm_reason", "context": ["sensor_result", "plant_data"],
            "question": f"How are the {target or 'plants'} doing based on this sensor data?",
            "store_as": "analysis"})
        children.append({"type": "respond", "message": resp})
        return {"type": "sequence", "label": f"Check {target or 'garden'}", "children": children}

    def _tree_simple(self, action, params, resp):
        return {"type": "sequence", "label": action, "children": [
            {"type": "condition", "name": "check_available"},
            {"type": "robot_action", "name": action, "params": params},
            {"type": "respond", "message": resp}
        ]}

    def _tree_general(self, question, resp):
        plant = self._extract_plant(question)
        return {"type": "sequence", "label": "General question", "children": [
            {"type": "function_call", "name": "get_plant_info",
             "params": {"plant_name": plant}, "store_as": "plant_info"},
            {"type": "function_call", "name": "fetch_weather",
             "params": {"location": self.garden.location_name, "days": 3}, "store_as": "weather"},
            {"type": "llm_reason", "context": ["plant_info", "weather"],
             "question": question, "store_as": "advice"},
            {"type": "respond", "message": "{advice}"}
        ]}

    def _tree_emergency(self):
        return {"type": "sequence", "label": "Emergency Stop", "children": [
            {"type": "robot_action", "name": "emergency_stop", "params": {}},
            {"type": "respond", "message": "Emergency stop! Robot halted."}
        ]}

    def _tree_respond(self, msg):
        return {"type": "respond", "message": msg}

    # -- Helpers ---------------------------------------

    def _extract_plant(self, text):
        for p in self.garden.plants:
            if p['name'].lower() in text.lower(): return p['name']
        return "general"

    def _is_emergency(self, text):
        t = text.strip().lower()
        for p in self.garden.safety.get('emergency_phrases', []):
            if t == p.lower() or t.startswith(p.lower()): return True
        return False

    def _parse_json_full(self, text):
        """Parse JSON that may contain nested arrays/objects."""
        text = re.sub(r'^```json\s*|^```\s*|\s*```$', '', text.strip()).strip()
        try: return json.loads(text)
        except: pass
        # Find outermost { ... } including nested braces
        depth = 0; start = -1
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0: start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try: return json.loads(text[start:i+1])
                    except: pass
        return None

    def reason(self, context, question):
        # Day 2: auto-fetch real weather so answers like "should I water?"
        # use actual rain forecasts instead of being hallucinated.
        if isinstance(context, dict) and "weather" not in context and \
                self.garden.latitude is not None and self.garden.longitude is not None:
            try:
                from .weather import get_weather
                weather = get_weather(self.garden.latitude, self.garden.longitude)
                if weather:
                    context = {**context, "weather": weather}
            except Exception as exc:  # network/parsing — never block the answer
                print(f"  weather fetch failed: {exc}")

        system = (
            "You are a warm gardening companion talking with an older "
            "person in their garden — like an experienced friend who "
            "drops by for tea, not a reference book. Speak naturally, "
            "briefly, and with care.\n"
            "\n"
            "How to answer:\n"
            "- Keep it SHORT. One or two sentences for casual questions. "
            "Never more than three. Drop anything that isn't the answer.\n"
            "- Sound like a friend, not a fact sheet. Conversational, warm.\n"
            "- One small practical hint is enough. Don't list everything "
            "you know about the plant.\n"
            "- Round numbers softly the way a person would say them aloud — "
            "'about 14 degrees', 'a fair bit of rain on Friday', 'breezy'. "
            "Never recite precise figures like '13.9 degrees Celsius' or "
            "'76 percent humidity' or '25.6 kilometres per hour'. Pick the "
            "one or two facts that actually matter to the answer.\n"
            "- For weather questions, pick the bit relevant to today or to "
            "the question — don't dump the full forecast.\n"
            "\n"
            "Don't:\n"
            "- No emojis, no exclamation marks.\n"
            "- No greetings like 'Hi there' or 'Hello'.\n"
            "- No follow-up questions, no 'let me know if you need more'.\n"
            "- Never invent weather numbers. If the context has weather, "
            "use it; if it doesn't, say you're not sure and move on."
        )
        prompt = (
            f"Context:\n{json.dumps(context, indent=2, default=str)}\n\n"
            f"Question: {question}\n\nAnswer:"
        )
        # Lower temperature and tighter max_tokens than before: warmth
        # is in the prompt, not in the sampling, and 180 tokens caps any
        # impulse to over-explain (was 350 with "comprehensive 3-5
        # sentences" — that's what produced the textbook-sized weather
        # replies the user flagged).
        r = self.llm.chat(system, prompt, temperature=0.4, max_tokens=180)
        return r if r else "I'm not sure about that."

    def is_available(self):
        return self.llm.is_available()
