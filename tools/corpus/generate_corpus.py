#!/usr/bin/env python3
"""
GrowMate voice-pipeline test corpus generator.

Scales the original 43-case suite to exactly 2000 cases, deterministically
(seeded), across 11 categories. The 43 originals are embedded verbatim as
seed cases (source="seed") so existing assertions keep passing.

Assertion conventions (mirroring the original TestCase suite):
  water <species>            -> ["@move:<species>", "D_W_1"]
  indirect water             -> ["@move:<species>"]        (water_smart may skip pump)
  move to <species>          -> ["M "]                     (absolute move, config position)
  go home                    -> ["H_0"]
  lights on/off              -> ["D_L_1"] / ["D_L_0"]
  photo (incl. of a plant)   -> ["I_1"]                    (move-to-plant not asserted, loose on purpose)
  panorama                   -> ["I_2"]
  weed scan                  -> ["I_4"]
  moisture sweep (all)       -> ["P_9"]
  per-plant sensor query     -> ["D_S_C"]
  water everything           -> ["D_W_1"]                  (full walk, not P_4 on a live map)
  emergency                  -> ["e"]
  refusal/negation/general/out_of_scope -> [] and, for negation-with-alternative,
                                a non-empty forbidden_commands list.
"""

import json
import random
import datetime

random.seed(42)

# ---------------------------------------------------------------- species ---

# gh1 (Maynooth) active map: species key -> spoken aliases
GH1 = {
    "spearmint": ["spearmint", "mint"],
    "tomato":    ["tomatoes", "tomato plants", "tomato bed"],
    "scallion":  ["scallions", "spring onions", "green onions"],
    "pepper":    ["peppers", "mixed peppers", "pepper bed"],
    "lettuce":   ["lettuce", "little gem lettuce", "lettuces"],
    "marigold":  ["marigold", "marigolds"],
    "basil":     ["basil", "basil plants"],
}

# farmbotdev-only species: valid there, refusals on gh1
OTHER_GH_SPECIES = ["geranium", "begonia", "dianthus", "cardinal flower", "lily"]

# Not in either map: must refuse cleanly (chosen to avoid alias collisions
# with map species, e.g. no "mint", "chillies", or "onions")
UNKNOWN_SPECIES = [
    "bananas", "strawberries", "carrots", "herbs", "roses", "cucumbers",
    "potatoes", "pumpkins", "sunflowers", "orchids", "daffodils", "tulips",
    "cabbage", "broccoli", "cauliflower", "spinach", "kale", "radishes",
    "beetroot", "celery", "leeks", "parsnips", "courgettes", "rhubarb",
    "blueberries", "raspberries", "grapevine", "corn", "ferns", "lavender",
]

# Plants usable in general-knowledge questions (map + common garden plants)
GENERAL_PLANTS = [
    "tomatoes", "lettuce", "basil", "peppers", "scallions", "marigolds",
    "spearmint", "carrots", "strawberries", "courgettes", "roses",
    "cucumbers", "potatoes", "onions", "garlic", "sweet peas", "dahlias",
    "runner beans",
]

# --------------------------------------------------------------- utilities --

def cap_first(s):
    return s[0].upper() + s[1:] if s else s

class Corpus:
    def __init__(self):
        self.cases = []
        self.seen = set()

    def add(self, category, subcategory, difficulty, utterance, expected_class,
            expected_commands, description, forbidden=None, source="generated"):
        key = " ".join(utterance.lower().split())
        if key in self.seen:
            return False
        self.seen.add(key)
        self.cases.append({
            "id": None,  # assigned at the end
            "category": category,
            "subcategory": subcategory,
            "difficulty": difficulty,
            "utterance": utterance,
            "expected_class": expected_class,
            "expected_commands": expected_commands,
            "forbidden_commands": forbidden or [],
            "description": description,
            "source": source,
        })
        return True

C = Corpus()

# ------------------------------------------------------------- seed cases ---
# The original 43, verbatim.

SEEDS = [
    ("water the spearmint", "robot_command", ["@move:spearmint", "D_W_1"], "Direct water", "direct", "water"),
    ("move to the lettuce", "robot_command", ["M "], "Direct move", "direct", "move"),
    ("go home", "robot_command", ["H_0"], "Go home", "direct", "home"),
    ("turn on the lights", "robot_command", ["D_L_1"], "Light on", "direct", "lights"),
    ("water the marigold", "robot_command", ["@move:marigold", "D_W_1"], "Water small bed", "direct", "water"),
    ("take a photo", "robot_command", ["I_1"], "Photo", "direct", "photo"),
    ("check moisture levels", "robot_command", ["P_9"], "Moisture", "direct", "moisture"),
    ("move to the scallions", "robot_command", ["M "], "Move scallion", "direct", "move"),
    ("scan for weeds", "robot_command", ["I_4"], "Weed scan", "direct", "weeds"),
    ("turn off the lights", "robot_command", ["D_L_0"], "Light off", "direct", "lights"),
    ("the spearmint seems dry", "robot_command", ["@move:spearmint"], "Indirect water", "indirect", "implied_water"),
    ("the peppers look thirsty", "robot_command", ["@move:pepper"], "Indirect water 2", "indirect", "implied_water"),
    ("give the lettuce a drink", "robot_command", ["@move:lettuce"], "Informal water", "indirect", "implied_water"),
    ("take care of the marigold", "robot_command", ["@move:marigold"], "Vague command", "indirect", "implied_water"),
    ("how are the tomatoes looking today", "robot_query", ["D_S_C"], "Status query", "query", "plant_status"),
    ("is the soil moist enough", "robot_query", ["P_9"], "Moisture query", "query", "moisture"),
    ("check on the lettuce for me", "robot_query", ["D_S_C"], "Check query", "query", "plant_status"),
    ("when should I plant basil", "general", [], "Planting advice", "general", "advice"),
    ("how often should I water tomatoes", "general", [], "Watering advice", "general", "advice"),
    ("what vegetables grow well in spring", "general", [], "Seasonal advice", "general", "advice"),
    ("stop", "emergency", ["e"], "E-stop", "emergency", "estop"),
    ("halt", "emergency", ["e"], "E-halt", "emergency", "estop"),
    ("emergency stop", "emergency", ["e"], "E-phrase", "emergency", "estop"),
    ("freeze", "emergency", ["e"], "E-freeze", "emergency", "estop"),
    ("water the spearmint and then go home", "robot_command", ["@move:spearmint", "D_W_1", "H_0"], "Multi", "multi", "water_then_home"),
    ("check on the marigold then water them", "robot_command", ["D_S_C"], "Multi q+c", "multi", "check_then_water"),
    ("water everything", "robot_command", ["D_W_1"], "Water all (long walk)", "safety", "water_all"),
    ("water the bananas", "refusal", [], "Unknown species", "refusal", "unknown_species"),
    ("water the strawberries", "refusal", [], "V1 ghost species", "refusal", "unknown_species"),
    ("move to the carrots", "refusal", [], "Unknown move", "refusal", "unknown_species"),
    ("water the herbs", "refusal", [], "V1 ghost species 2", "refusal", "unknown_species"),
    ("walter the spearmint", "robot_command", ["@move:spearmint"], "STT noise water->Walter", "hard", "stt_noise"),
    ("could you please water the marigold for me dear", "robot_command", ["@move:marigold"], "Politeness wrapper", "hard", "politeness"),
    ("um can you uh water the spearmint please", "robot_command", ["@move:spearmint"], "Fillers", "hard", "fillers"),
    ("the marigold looks a bit sad maybe give it some water", "robot_command", ["@move:marigold"], "Hedged indirect", "hard", "hedged"),
    ("i think the pepper bed could use a drink", "robot_command", ["@move:pepper"], "Vague informal", "hard", "hedged"),
    ("pop over to the lettuce and see how it's doing", "robot_query", ["D_S_C"], "Informal check", "hard", "colloquial"),
    ("my knees hurt too much to water the spearmint today can you do it", "robot_command", ["@move:spearmint"], "Elderly context", "hard", "elderly_context"),
    ("give the marigold a little sprinkle would you", "robot_command", ["@move:marigold"], "Colloquial water", "hard", "colloquial"),
    ("it's getting dark put the lights on love", "robot_command", ["D_L_1"], "Colloquial lights", "hard", "colloquial"),
    ("take a photo for me love", "robot_command", ["I_1"], "Colloquial photo", "hard", "colloquial"),
    ("don't water the tomatoes", "negation", [], "Direct negation", "negation", "direct_negation", ["@move:tomato", "D_W_1"]),
    ("no need to water anything today", "negation", [], "Blanket negation", "negation", "blanket_negation", ["D_W_1"]),
]

for s in SEEDS:
    forbidden = s[6] if len(s) > 6 else None
    C.add(s[4], s[5], "easy" if s[4] not in ("hard", "multi") else "hard",
          s[0], s[1], s[2], s[3], forbidden=forbidden, source="seed")

# ------------------------------------------------------- pool: direct -------

pool_direct = []

WATER_T = [
    "water the {a}",
    "please water the {a}",
    "can you water the {a}",
    "water the {a} now",
    "give the {a} some water",
    "go and water the {a}",
    "water the {a} for me",
    "put some water on the {a}",
    "give some water to the {a}",
    "would you water the {a}",
]
for sp, aliases in GH1.items():
    for a in aliases:
        for t in WATER_T:
            pool_direct.append(("water", t.format(a=a), "robot_command",
                                [f"@move:{sp}", "D_W_1"], f"Direct water ({sp})"))

MOVE_T = [
    "move to the {a}", "go to the {a}", "go over to the {a}",
    "head to the {a}", "drive to the {a}", "move over to the {a}",
]
for sp, aliases in GH1.items():
    for a in aliases:
        for t in MOVE_T:
            pool_direct.append(("move", t.format(a=a), "robot_command",
                                ["M "], f"Direct move ({sp})"))

for u in ["go home", "return home", "go back home", "head home",
          "go to the home position", "park yourself", "return to base",
          "go back to the start", "move to home", "back to home position"]:
    pool_direct.append(("home", u, "robot_command", ["H_0"], "Go home"))

for u in ["turn on the lights", "turn the lights on", "switch on the lights",
          "lights on", "put the lights on", "switch the lights on",
          "turn on the led strip", "can you turn the lights on",
          "put the grow lights on", "please turn on the lights"]:
    pool_direct.append(("lights", u, "robot_command", ["D_L_1"], "Lights on"))

for u in ["turn off the lights", "turn the lights off", "switch off the lights",
          "lights off", "put the lights off", "switch the lights off",
          "turn off the led strip", "can you turn the lights off",
          "kill the lights", "please turn off the lights"]:
    pool_direct.append(("lights", u, "robot_command", ["D_L_0"], "Lights off"))

for u in ["take a photo", "take a picture", "take a snap", "snap a photo",
          "take a photo please", "grab a photo", "take a picture for me",
          "can you take a photo", "photograph the current position"]:
    pool_direct.append(("photo", u, "robot_command", ["I_1"], "Photo here"))

PHOTO_SP_T = ["take a photo of the {a}", "take a picture of the {a}",
              "get a photo of the {a}", "snap the {a} for me"]
for sp, aliases in GH1.items():
    for a in aliases[:2]:
        for t in PHOTO_SP_T:
            pool_direct.append(("photo_of_plant", t.format(a=a), "robot_command",
                                ["I_1"], f"Photo of {sp} (loose assertion, I_1 only)"))

for u in ["take a panorama", "do a panorama", "take a panorama of the bed",
          "photograph the whole bed", "take a full panorama",
          "give me a panorama of the greenhouse", "run a panorama sequence"]:
    pool_direct.append(("panorama", u, "robot_command", ["I_2"], "Panorama"))

for u in ["scan for weeds", "check for weeds", "look for weeds",
          "do a weed scan", "run weed detection", "any weeds around here",
          "detect weeds", "see if there are any weeds", "weed check please"]:
    pool_direct.append(("weeds", u, "robot_command", ["I_4"], "Weed scan"))

WEEDS_SP_T = ["check for weeds around the {a}", "scan the {a} for weeds",
              "any weeds near the {a}"]
for sp, aliases in GH1.items():
    for a in aliases[:1]:
        for t in WEEDS_SP_T:
            pool_direct.append(("weeds", t.format(a=a), "robot_command",
                                ["I_4"], f"Weed scan near {sp}"))

for u in ["check moisture levels", "read the soil moisture everywhere",
          "check the moisture of all the plants", "do a moisture sweep",
          "measure soil moisture across the bed", "check soil moisture levels",
          "how moist is the soil check everywhere", "run a full moisture check"]:
    pool_direct.append(("moisture", u, "robot_command", ["P_9"], "Moisture sweep"))

# ------------------------------------------------------ pool: indirect ------

pool_indirect = []
INDIRECT_T = [
    "the {a} seems dry", "the {a} looks dry", "the {a} looks thirsty",
    "the {a} seems thirsty", "give the {a} a drink",
    "the {a} could use some water", "the {a} could do with a drink",
    "take care of the {a}", "the {a} is looking parched",
    "the soil around the {a} is bone dry", "the {a} needs watering",
    "i think the {a} needs a drink", "the {a} is wilting a bit",
    "the {a} hasn't had water in a while",
]
for sp, aliases in GH1.items():
    for a in aliases:
        for t in INDIRECT_T:
            pool_indirect.append(("implied_water", t.format(a=a), "robot_command",
                                  [f"@move:{sp}"], f"Indirect water ({sp})"))

# --------------------------------------------------------- pool: query ------

pool_query = []
QUERY_SP_T = [
    "how are the {a} looking today", "how is the {a} doing",
    "check on the {a} for me", "check on the {a}",
    "is the {a} soil moist enough", "how dry is the soil at the {a}",
    "what's the moisture like at the {a}", "is the {a} okay",
    "does the {a} need anything", "how's the {a} getting on",
    "do the {a} need water", "is the {a} dry",
    "give me a reading on the {a}", "what's the soil like at the {a}",
]
for sp, aliases in GH1.items():
    for a in aliases:
        for t in QUERY_SP_T:
            pool_query.append(("plant_status", t.format(a=a), "robot_query",
                               ["D_S_C"], f"Status query ({sp})"))

for u in ["is the soil moist enough", "is the soil dry", "how moist is the soil",
          "what are the moisture levels like", "is anything too dry",
          "does anything need water", "is the bed dry",
          "how's the soil looking overall", "give me a moisture report",
          "what's the driest part of the bed"]:
    pool_query.append(("moisture", u, "robot_query", ["P_9"], "Global moisture query"))

# -------------------------------------------------------- pool: general -----

pool_general = []
GENERAL_PLANT_T = [
    "when should i plant {p}", "how often should i water {p}",
    "what's the best soil for {p}", "how much sun does {p} need",
    "how do i deal with aphids on {p}", "when do i harvest {p}",
    "how far apart should i plant {p}", "can i grow {p} over winter",
    "what's a good companion plant for {p}",
    "why are the leaves on my {p} turning yellow",
    "how do i take cuttings from {p}", "how do i stop slugs eating my {p}",
    "when should i prune {p}", "can i grow {p} from seed",
    "should i feed my {p} every week",
]
for p in GENERAL_PLANTS:
    for t in GENERAL_PLANT_T:
        pool_general.append(("advice", t.format(p=p), "general", [],
                             f"General advice ({p})"))

for u in ["what vegetables grow well in spring", "how do i improve clay soil",
          "what's the difference between compost and mulch",
          "when is the last frost usually in ireland", "how do i test soil ph",
          "what does npk mean on fertiliser bags", "how do i harden off seedlings",
          "is it too late to sow lettuce in july",
          "what should i be doing in the garden in october",
          "how do i make my own compost", "what is crop rotation",
          "why is drainage important for pots", "what is bolting in lettuce",
          "how do i attract bees to the garden", "what is hardening off",
          "should i water in the morning or evening",
          "what's the ideal greenhouse temperature for seedlings",
          "how do i know when soil is too wet", "what is damping off",
          "will it rain tomorrow do i need to water",
          "what herbs are easiest for beginners",
          "how deep should i sow seeds generally",
          "what's eating holes in my leaves",
          "how do i get rid of greenfly naturally",
          "when should i start seeds indoors"]:
    pool_general.append(("advice", u, "general", [], "General gardening advice"))

# ------------------------------------------------------ pool: emergency -----

pool_emergency = []
for u in ["stop", "halt", "freeze", "emergency stop", "stop now", "stop it",
          "stop everything", "stop right there", "stop right now", "abort",
          "abort abort", "stop the machine", "stop the robot", "make it stop",
          "whoa whoa stop", "no no stop", "stop please", "please stop",
          "hold it right there", "stop moving", "don't move", "cut it out stop",
          "STOP", "Stop!", "stop stop stop", "e-stop", "hit the emergency stop",
          "everything stop", "quit moving now", "stop that right now",
          "it's going to hit the tray stop", "the arm is about to crash stop it",
          "stop before you hit something", "wait stop", "hang on stop",
          "stop I dropped my glasses in the bed", "STOP NOW", "halt everything",
          "freeze right there", "stop the gantry", "shut it down now",
          "emergency", "that's an emergency stop", "full stop now",
          "stop stop"]:
    pool_emergency.append(("estop", u, "emergency", ["e"], "Emergency stop"))

# ---------------------------------------------------------- pool: multi -----

pool_multi = []
SP = list(GH1.keys())

for sp in SP:
    a = GH1[sp][0]
    for t in ["water the {a} and then go home", "water the {a} then come home",
              "water the {a} and go back home"]:
        pool_multi.append(("water_then_home", t.format(a=a), "robot_command",
                           [f"@move:{sp}", "D_W_1", "H_0"], f"Water {sp} then home"))
    for t in ["water the {a} and take a photo", "water the {a} then snap a picture",
              "water the {a} and photograph it"]:
        pool_multi.append(("water_then_photo", t.format(a=a), "robot_command",
                           [f"@move:{sp}", "D_W_1", "I_1"], f"Water {sp} then photo"))
    for t in ["check on the {a} then water them", "check the {a} and water if needed"]:
        pool_multi.append(("check_then_water", t.format(a=a), "robot_command",
                           ["D_S_C"], f"Check {sp} then water"))
    for t in ["turn on the lights and take a photo of the {a}"]:
        pool_multi.append(("lights_then_photo", t.format(a=a), "robot_command",
                           ["D_L_1", "I_1"], f"Lights then photo of {sp}"))
    for t in ["water the {a} and turn off the lights"]:
        pool_multi.append(("water_then_lights", t.format(a=a), "robot_command",
                           [f"@move:{sp}", "D_W_1", "D_L_0"], f"Water {sp}, lights off"))
    for t in ["water the {a} and scan for weeds"]:
        pool_multi.append(("water_then_weeds", t.format(a=a), "robot_command",
                           [f"@move:{sp}", "D_W_1", "I_4"], f"Water {sp}, weed scan"))
    for t in ["take a photo of the {a} then go home",
              "go to the {a} and take a picture then come home"]:
        pool_multi.append(("photo_then_home", t.format(a=a), "robot_command",
                           ["I_1", "H_0"], f"Photo of {sp} then home"))
    for t in ["water the {a} check for weeds and go home"]:
        pool_multi.append(("water_weeds_home", t.format(a=a), "robot_command",
                           [f"@move:{sp}", "D_W_1", "I_4", "H_0"],
                           f"Water {sp}, weeds, home"))

for s1 in SP:
    for s2 in SP:
        if s1 == s2:
            continue
        a1, a2 = GH1[s1][0], GH1[s2][0]
        pool_multi.append(("water_two", f"water the {a1} and the {a2}",
                           "robot_command",
                           [f"@move:{s1}", "D_W_1", f"@move:{s2}", "D_W_1"],
                           f"Water {s1} and {s2}"))

for u, cmds, d in [
    ("take a photo and then go home", ["I_1", "H_0"], "Photo then home"),
    ("take a picture then come back home", ["I_1", "H_0"], "Photo then home"),
    ("turn on the lights take a photo and turn them off again",
     ["D_L_1", "I_1", "D_L_0"], "Lights, photo, lights off"),
    ("scan for weeds and then go home", ["I_4", "H_0"], "Weeds then home"),
    ("check moisture levels then go home", ["P_9", "H_0"], "Moisture then home"),
    ("do a panorama and then park yourself", ["I_2", "H_0"], "Panorama then home"),
]:
    pool_multi.append(("chain", u, "robot_command", cmds, d))

# --------------------------------------------------------- pool: safety -----

pool_safety = []
for u in ["water everything", "water all the plants", "water the whole bed",
          "give everything a drink", "water the entire greenhouse",
          "water every plant", "do a full watering run",
          "water the lot", "give all the plants some water",
          "water everything in the bed", "run a watering of everything",
          "please water all of them", "water the whole greenhouse for me",
          "everything needs water do them all", "give the whole bed a soak",
          "water every single plant", "full watering please",
          "do the full watering round"]:
    pool_safety.append(("water_all", u, "robot_command", ["D_W_1"],
                        "Water all (long walk)"))

# -------------------------------------------------------- pool: refusal -----

pool_refusal = []
REFUSAL_T = ["water the {p}", "move to the {p}", "go to the {p}",
             "check on the {p}", "give the {p} a drink", "how are the {p} doing"]
for p in UNKNOWN_SPECIES:
    for t in REFUSAL_T:
        pool_refusal.append(("unknown_species", t.format(p=p), "refusal", [],
                             f"Unknown species ({p})"))
for p in OTHER_GH_SPECIES:
    for t in REFUSAL_T[:5]:
        pool_refusal.append(("wrong_greenhouse", t.format(p=p), "refusal", [],
                             f"farmbotdev species, not in gh1 map ({p})"))

# ------------------------------------------------------- pool: negation -----

pool_negation = []
NEG_T = [
    ("don't water the {a}", "Direct negation"),
    ("do not water the {a}", "Direct negation"),
    ("you don't need to water the {a} today", "Direct negation"),
    ("the {a} doesn't need water", "Statement negation"),
    ("skip the {a} today", "Skip negation"),
    ("leave the {a} alone today", "Skip negation"),
    ("no need to water the {a}", "No-need negation"),
    ("hold off on watering the {a}", "Hold-off negation"),
    ("the {a} has had enough water", "Statement negation"),
    ("never mind about the {a}", "Never-mind negation"),
    ("i already watered the {a} myself", "Already-done negation"),
    ("whatever you do don't water the {a}", "Emphatic negation"),
]
for sp, aliases in GH1.items():
    for a in aliases[:2]:
        for t, d in NEG_T:
            pool_negation.append(("plant_negation", t.format(a=a), "negation", [],
                                  f"{d} ({sp})",
                                  [f"@move:{sp}", "D_W_1"]))

for u in ["no need to water anything today", "don't water anything",
          "nothing needs watering today", "skip the watering today",
          "no watering today please", "everything's been watered already",
          "it rained so don't water anything", "leave the watering for today",
          "hold off on all watering", "don't do the watering round today",
          "no need to do anything today", "we're grand for water today"]:
    pool_negation.append(("blanket_negation", u, "negation", [],
                          "Blanket negation", ["D_W_1"]))

# ---------------------------------------------------- pool: out_of_scope ----

OOS_BASE = {
    "household_physical": [
        "help me move the desk", "bring me my glasses", "make me a cup of tea",
        "do the washing up", "vacuum the sitting room", "close the curtains",
        "open the front door", "feed the cat", "let the dog out",
        "carry the shopping in from the car", "put the kettle on",
        "iron my shirt", "hang the washing out", "empty the bins",
        "change the lightbulb in the kitchen", "fix the leaky tap",
        "mop the kitchen floor", "set the table for dinner",
        "fold the laundry", "answer the door for me",
        "bring in the washing before it rains", "lock the back door",
        "find my reading glasses", "water the houseplants in the kitchen",
    ],
    "digital": [
        "switch on the computer", "help me code an array sum python script",
        "write an email to my son", "set an alarm for 7am", "play some music",
        "turn on the tv", "call my daughter", "what's on telly tonight",
        "order my groceries online", "print this document",
        "check my bank balance", "book a doctor's appointment for me",
        "send a text to mary", "look up the bus timetable",
        "turn up the radio", "record the match tonight",
        "reset the wifi router", "charge my phone", "open youtube",
        "write me a shopping list app",
    ],
    "garden_adjacent_impossible": [
        "mow the lawn", "trim the hedge", "rake the leaves",
        "prune the apple tree out front", "water the hanging baskets on the porch",
        "repot the ficus in the living room", "plant a tree by the gate",
        "build a raised bed", "fix the greenhouse door", "paint the fence",
        "clean the gutters", "harvest the tomatoes and bring them inside",
        "pick some lettuce for tonight's salad", "spread mulch on the flower beds",
        "turn the compost heap", "sharpen the shears", "put slug pellets down",
        "move the water butt", "sweep the garden path", "net the fruit bushes",
        "weed the front driveway", "dig over the vegetable patch",
        "water the pots on the patio", "tie up the runner beans out the front",
        "clean the greenhouse glass", "stake the dahlias by the wall",
    ],
    "unsafe_or_nonsense": [
        "spray water on the electrical socket", "drive into the greenhouse wall",
        "go as fast as you can and don't stop for anything",
        "water the plug sockets", "water my phone",
        "run over to the beehive and poke it", "spray water at the fuse box",
        "climb out of your frame", "water the ceiling",
        "smash through the glass",
    ],
    "chit_chat": [
        "what's the capital of france", "tell me a joke",
        "who won the football last night", "what time is it in australia",
        "how do i make soda bread", "what's the meaning of life",
        "read me the news headlines", "what's the lotto numbers",
        "sing me a song", "how old are you",
        "what's the weather like in spain", "recommend a good book",
        "what year did ireland join the eu", "how do i renew my passport",
    ],
}

QUESTION_STARTS = {"what", "what's", "who", "how", "when", "where", "why",
                   "will", "is", "recommend", "tell", "sing", "read"}

def oos_wrap(u):
    yield u
    if u.split()[0] not in QUESTION_STARTS:
        yield f"could you {u} for me please"
        yield f"would you be able to {u} love"

pool_oos = []
for sub, items in OOS_BASE.items():
    for base in items:
        for u in oos_wrap(base):
            pool_oos.append((sub, u, "out_of_scope", [],
                             f"Out of scope ({sub.replace('_', ' ')})"))

# ----------------------------------------------------------- pool: hard -----

pool_hard = []

HOMOPHONES = {
    "water": ["walter", "warter", "wadder", "warder"],
    "spearmint": ["spear mint", "spare mint", "sphere mint"],
    "marigold": ["mary gold", "merry gold", "marry gold"],
    "marigolds": ["mary golds", "merry golds"],
    "lettuce": ["let us", "lettice"],
    "tomatoes": ["tomatos", "tamatoes", "toe may toes"],
    "scallions": ["stallions", "scallians"],
    "peppers": ["peppas", "pepers"],
    "basil": ["bazil", "basel"],
    "lights": ["lites"],
    "photo": ["foto"],
    "moisture": ["moisher", "moysture"],
    "weeds": ["wheats", "weads"],
    "mint": ["minte"],
}

def corrupt(u):
    words = u.split()
    idxs = [i for i, w in enumerate(words) if w.lower() in HOMOPHONES]
    if not idxs:
        return None
    i = random.choice(idxs)
    words[i] = random.choice(HOMOPHONES[words[i].lower()])
    return " ".join(words)

FILLERS = ["um", "uh", "er", "ehm", "you know", "like"]

def add_fillers(u):
    words = u.split()
    n = random.choice([1, 2])
    for _ in range(n):
        pos = random.randint(0, len(words))
        words.insert(pos, random.choice(FILLERS))
    return " ".join(words)

POLITE_PRE = ["could you please", "would you mind", "if it's not too much trouble could you",
              "be a dear and", "when you get a chance can you", "sorry to bother you but could you"]
POLITE_SUF = ["for me dear", "would you love", "please pet", "if you don't mind",
              "there's a good lad", "thanks a million"]

def politeify(u):
    if random.random() < 0.5:
        return f"{random.choice(POLITE_PRE)} {u}"
    return f"{u} {random.choice(POLITE_SUF)}"

ELDERLY_PRE = [
    "my knees are at me today so can you",
    "my back is acting up would you",
    "i can't get down the garden today can you",
    "the hip is giving me trouble so you'll have to",
    "i'm not able for the watering can anymore can you",
    "doctor says i'm to take it easy so can you",
    "it's too cold out for me will you",
    "i left my stick inside so can you",
]

def elderlyfy(u):
    return f"{random.choice(ELDERLY_PRE)} {u}"

def repeatify(u):
    words = u.split()
    i = random.randint(0, len(words) - 1)
    words.insert(i, words[i])
    return " ".join(words)

# Base bank of (utterance, expected_class, cmds) to transform
hard_bases = []
for sp, aliases in GH1.items():
    a = aliases[0]
    hard_bases.append((f"water the {a}", "robot_command", [f"@move:{sp}"]))
    hard_bases.append((f"give the {a} some water", "robot_command", [f"@move:{sp}"]))
    hard_bases.append((f"check on the {a}", "robot_query", ["D_S_C"]))
    hard_bases.append((f"the {a} needs watering", "robot_command", [f"@move:{sp}"]))
hard_bases += [
    ("turn on the lights", "robot_command", ["D_L_1"]),
    ("turn off the lights", "robot_command", ["D_L_0"]),
    ("take a photo", "robot_command", ["I_1"]),
    ("go home", "robot_command", ["H_0"]),
    ("scan for weeds", "robot_command", ["I_4"]),
    ("check moisture levels", "robot_command", ["P_9"]),
]

TRANSFORMS = [
    ("stt_noise", corrupt, "STT homophone corruption"),
    ("fillers", add_fillers, "Disfluency fillers"),
    ("politeness", politeify, "Politeness wrapper"),
    ("elderly_context", elderlyfy, "Elderly context preamble"),
    ("stutter", repeatify, "Repeated word / stutter"),
]

for _ in range(400):  # oversample heavily; dedupe trims
    for sub, fn, desc in TRANSFORMS:
        base_u, cls, cmds = random.choice(hard_bases)
        out = fn(base_u)
        if out:
            pool_hard.append((sub, out, cls, cmds, desc))

# Hand-written colloquial and self-correction hard cases
HARD_HAND = [
    ("colloquial", "give the tomatoes a wee drop of water", "robot_command", ["@move:tomato"], "Colloquial water"),
    ("colloquial", "throw a sup of water on the basil there", "robot_command", ["@move:basil"], "Colloquial water"),
    ("colloquial", "give the lettuces a splash would you", "robot_command", ["@move:lettuce"], "Colloquial water"),
    ("colloquial", "wet the marigolds there for me", "robot_command", ["@move:marigold"], "Colloquial water"),
    ("colloquial", "give the mint a little sprinkle", "robot_command", ["@move:spearmint"], "Colloquial water"),
    ("colloquial", "pop over and see how the tomatoes are getting on", "robot_query", ["D_S_C"], "Colloquial check"),
    ("colloquial", "have a look at the peppers for me", "robot_query", ["D_S_C"], "Colloquial check"),
    ("colloquial", "it's gone dark in here stick the lights on", "robot_command", ["D_L_1"], "Colloquial lights"),
    ("colloquial", "we're done for the day lights out", "robot_command", ["D_L_0"], "Colloquial lights off"),
    ("colloquial", "off home with you now", "robot_command", ["H_0"], "Colloquial home"),
    ("colloquial", "get a snap of the garden for me", "robot_command", ["I_1"], "Colloquial photo"),
    ("self_correction", "water the tomatoes no wait i mean the peppers", "robot_command", ["@move:pepper"], "Self correction", ["@move:tomato"]),
    ("self_correction", "check on the basil actually no check the lettuce", "robot_query", ["D_S_C"], "Self correction"),
    ("self_correction", "water the mari the marigolds i mean", "robot_command", ["@move:marigold"], "Restart mid-word"),
    ("self_correction", "go to the scal the scallions", "robot_command", ["M "], "Restart mid-word"),
    ("self_correction", "turn on the no turn off the lights", "robot_command", ["D_L_0"], "Self correction", ["D_L_1"]),
    ("run_on", "right so it's very warm today and i was thinking the spearmint is probably gasping so water it there", "robot_command", ["@move:spearmint"], "Run-on with buried command"),
    ("run_on", "i was out yesterday and the marigolds looked grand but today they're drooping give them some water", "robot_command", ["@move:marigold"], "Run-on with buried command"),
    ("run_on", "mary was saying her tomatoes are huge this year anyway can you check on ours", "robot_query", ["D_S_C"], "Run-on with buried query"),
    ("run_on", "the grandkids are coming saturday take a nice photo of the garden for them", "robot_command", ["I_1"], "Run-on with buried command"),
    ("ambiguous_resolved", "the ones by the mint look dry water them", "robot_command", ["D_W_1"], "Vague target, watering intent"),
    ("ambiguous_resolved", "do the usual watering for the tomatoes", "robot_command", ["@move:tomato"], "Routine reference"),
]
for h in HARD_HAND:
    forbidden = h[5] if len(h) > 5 else None
    pool_hard.append((h[0], h[1], h[2], h[3], h[4], forbidden))

# ------------------------------------------------------------ assembly ------

CAPS = {
    "direct": 380, "indirect": 200, "query": 180, "general": 190,
    "emergency": 40, "multi": 140, "safety": 15, "refusal": 175,
    "hard": 340, "negation": 100, "out_of_scope": 240,
}
assert sum(CAPS.values()) == 2000

POOLS = {
    "direct": pool_direct, "indirect": pool_indirect, "query": pool_query,
    "general": pool_general, "emergency": pool_emergency, "multi": pool_multi,
    "safety": pool_safety, "refusal": pool_refusal, "hard": pool_hard,
    "negation": pool_negation, "out_of_scope": pool_oos,
}

DIFFICULTY = {
    "direct": "easy", "indirect": "medium", "query": "easy", "general": "easy",
    "emergency": "easy", "multi": "medium", "safety": "medium",
    "refusal": "medium", "hard": "hard", "negation": "hard",
    "out_of_scope": "medium",
}

# Items that must survive sampling: hand-written hard cases and blanket negations
PRIORITY = {
    "hard": set(h[1] for h in HARD_HAND),
    "negation": set(u for (sub, u, *_rest) in pool_negation if sub == "blanket_negation"),
}

for cat, cap in CAPS.items():
    seeded = sum(1 for c in C.cases if c["category"] == cat)
    need = cap - seeded
    pool = POOLS[cat][:]
    random.shuffle(pool)
    prio = PRIORITY.get(cat)
    if prio:
        pool.sort(key=lambda item: item[1] not in prio)  # stable: priority first
    added = 0
    for item in pool:
        if added >= need:
            break
        if cat == "negation":
            sub, u, cls, cmds, desc, forb = (item + (None,))[:6] if len(item) == 5 else item[:6]
            sub, u, cls, cmds, desc, forb = item[0], item[1], item[2], item[3], item[4], item[5]
        else:
            sub, u, cls, cmds, desc = item[0], item[1], item[2], item[3], item[4]
            forb = item[5] if len(item) > 5 else None
        if C.add(cat, sub, DIFFICULTY[cat], u, cls, cmds, desc, forbidden=forb):
            added += 1
    if added < need:
        raise SystemExit(f"Pool exhausted for {cat}: needed {need}, got {added} "
                         f"(pool size {len(POOLS[cat])})")

# Stable ordering: category order, then utterance; then assign ids
CAT_ORDER = list(CAPS.keys())
C.cases.sort(key=lambda c: (CAT_ORDER.index(c["category"]), c["source"] != "seed", c["utterance"]))
for i, c in enumerate(C.cases, 1):
    c["id"] = f"GM-{i:04d}"

counts = {}
for c in C.cases:
    counts[c["category"]] = counts.get(c["category"], 0) + 1

corpus = {
    "metadata": {
        "name": "GrowMate voice-pipeline test corpus",
        "version": "1.0",
        "generated": datetime.date.today().isoformat(),
        "generator": "generate_corpus.py (seed=42, deterministic)",
        "total_cases": len(C.cases),
        "seed_cases": sum(1 for c in C.cases if c["source"] == "seed"),
        "target_greenhouse": "gh1 (Maynooth)",
        "active_species_gh1": list(GH1.keys()),
        "farmbotdev_species_treated_as_refusals_on_gh1": OTHER_GH_SPECIES,
        "expected_classes": ["robot_command", "robot_query", "general",
                             "emergency", "refusal", "negation", "out_of_scope"],
        "category_counts": counts,
        "assertion_notes": {
            "@move:<species>": "pipeline moved to that species (any plant of it)",
            "M ": "an absolute move command was emitted",
            "photo_of_plant": "asserts I_1 only; tighten to @move + I_1 if your pipeline moves first",
            "forbidden_commands": "must NOT appear in the emitted sequence (negation and self-correction cases)",
            "refusal/negation/general/out_of_scope": "terminal success requires zero robot commands",
        },
    },
    "cases": C.cases,
}

with open(str(__import__("pathlib").Path(__file__).parent / "growmate_test_corpus.json"), "w") as f:
    json.dump(corpus, f, indent=2, ensure_ascii=False)

print(f"total: {len(C.cases)}")
for k in CAT_ORDER:
    print(f"  {k:14s} {counts[k]}")
print("unique utterances:", len({c['utterance'].lower() for c in C.cases}))
