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

# ---------------------------------------------------------------- tools -----
# mount_tool / stow_tool. Added 2026-07 and deliberately ADDITIVE: this block
# must not change any of the original 2000 cases, so it
#   (a) runs on its own seeded stream and restores the global one after, and
#   (b) is capped as a NEW category appended last in CAPS, so it displaces
#       nothing and its cases sort to GM-2001+ leaving existing ids stable.
# Without (a) every random.choice/shuffle below would shift and the whole
# corpus would silently re-roll.
#
# The spread matters as much as the coverage: an older gardener asks for the
# nozzle in many unexpected ways, so the same TRANSFORMS the hard category uses
# (STT homophones, fillers, politeness, elderly preamble, stutter) are applied
# here rather than hand-writing flat phrasings.

# Bays are from gh1.yaml, the corpus's target robot; mounting bay N publishes
# T_N_1 and releasing it publishes T_N_2. test_wire_grammar asserts every
# configured index is mountable, so these stay honest.
TOOL_HEADS = {
    "watering nozzle": 2, "nozzle": 2, "water nozzle": 2,
    "soil probe": 1, "soil sensor": 1, "probe": 1,
    "weeder": 4,
    "seeder": 3,
}
STOW_BAY = 2  # the harness parks watering_nozzle before each stow case

# Sound like a tool, aren't on this robot -> clean refusal, and must never
# move the UTM.
NOT_TOOLS = ["jackhammer", "lawnmower", "hedge trimmer", "shovel", "rake",
             "strimmer", "chainsaw", "leaf blower", "pruning shears", "trowel"]

MOUNT_T = ["pick up the {t}", "get the {t}", "fetch the {t}", "put the {t} on",
           "grab the {t}", "mount the {t}", "put on the {t}", "i need the {t}",
           "can you get the {t}", "attach the {t}", "bring the {t} over",
           "load up the {t}"]
STOW_T = ["put it back", "put the tool back", "put that back", "stow the tool",
          "take it off", "take the tool off", "put it away",
          "put the tool away", "pop it back in the bay", "take that head off",
          "back in the holder please", "you can put that down now",
          "drop the tool off", "return the tool to its slot"]
NOT_TOOL_T = ["pick up the {t}", "get the {t}", "fetch me the {t}",
              "put the {t} on", "grab the {t}"]

# Tool words the STT mishears. Kept OUT of the shared HOMOPHONES dict at module
# scope: "watering" occurs in hard_bases, so adding it there would change which
# word corrupt() picks for existing hard cases and re-roll them.
TOOL_HOMOPHONES = {
    "nozzle": ["nozzel", "nozle", "nossle"],
    "probe": ["prob", "probes", "prowl"],
    "weeder": ["weeda", "reader", "wheeler"],
    "seeder": ["seeda", "cedar", "sealer"],
    "tool": ["toole", "tull"],
    "soil": ["soyle", "sile"],
}

pool_tool = []
tool_priority = set()

_rand_state = random.getstate()
_homophones_backup = dict(HOMOPHONES)
random.seed(4242)
HOMOPHONES.update(TOOL_HOMOPHONES)

tool_bases = []
for _spoken, _bay in TOOL_HEADS.items():
    for _tmpl in MOUNT_T:
        tool_bases.append((_tmpl.format(t=_spoken), "robot_command", [f"T_{_bay}_1"]))
for _tmpl in STOW_T:
    tool_bases.append((_tmpl, "robot_command", [f"T_{STOW_BAY}_2"]))

for _u, _cls, _cmds in tool_bases:
    pool_tool.append(("plain", _u, _cls, _cmds, "Tool head request", None))

# Refusals: never mount a different head just because the name was close.
for _nt in NOT_TOOLS:
    for _tmpl in NOT_TOOL_T:
        _u = _tmpl.format(t=_nt)
        pool_tool.append(("unknown_tool", _u, "refusal", [],
                          "Tool this robot doesn't have", ["T_"]))
        tool_priority.add(_u)

# Guarantee a baseline, but keep it SMALL. The priority set is taken before the
# shuffle, so if it approaches the cap it crowds out the transformed spread
# entirely — which is the whole point of the category. Baseline is ~20% of the
# cap; the rest is the many-ways-of-asking mix.
for _spoken in list(TOOL_HEADS)[:8]:
    tool_priority.add(f"pick up the {_spoken}")
for _tmpl in STOW_T[:4]:
    tool_priority.add(_tmpl)
for _nt in NOT_TOOLS[:12]:
    tool_priority.add(f"pick up the {_nt}")

for _ in range(400):  # oversample; dedupe + the cap trim it
    for _sub, _fn, _desc in TRANSFORMS:
        _base_u, _cls, _cmds = random.choice(tool_bases)
        _out = _fn(_base_u)
        if _out:
            pool_tool.append((_sub, _out, _cls, _cmds, _desc, None))

HOMOPHONES.clear()
HOMOPHONES.update(_homophones_backup)
random.setstate(_rand_state)

# -------------------------------------------------- wave 2: demo-derived ----
# 380 cases (-> 2500) written against what older adults ACTUALLY said at the
# 2026-06/07 community demonstrations, not against what a generator imagines
# they'd say. Everything here is a speech PATTERN abstracted from the demo
# sessions: no participant name, personal circumstance, or verbatim personal
# remark is reproduced. (The transcripts themselves are gitignored personal
# data; this file is on a public remote. Patterns are not personal data —
# quotes would be.)
#
# Why this wave exists — the generated transforms above are mechanical, and the
# demo showed three specific ways that costs realism:
#
# 1. THE HOMOPHONES ARE INVENTED AND WRONG. The generator guesses the STT hears
#    "walter"/"wadder" for "water". What it ACTUALLY produced in the field was
#    "butter" ("I heard you say butter the tomatoes"), and "lettuce" came back
#    as "letters" / "lattices" repeatedly across two separate sessions. None of
#    those three strings appear anywhere in the first 2120 cases: the corpus was
#    testing errors the system doesn't make and ignoring the ones it does.
# 2. DISFLUENCY ISN'T UNIFORM. repeatify() duplicates a random word. Real
#    speakers stumble on the HARD word — the plant name. One attendee: asked for
#    the lettuces, stopped, said she didn't know what she'd called it, then said
#    it twice more. The stumble is always on the species.
# 3. POLITENESS IS LOCAL. The observed wrapper is Irish-English and trails the
#    command ("for us please", "would you ever"), rather than the generic
#    prefixes above.
#
# These cases are tagged source="demo_observed" and sort LAST, so they take
# GM-2121+ and every existing id stays put.

DEMO_SPECIES = [
    ("spearmint", "spearmint"), ("spearmint", "mint"),
    ("tomato", "tomatoes"), ("tomato", "tomato plants"),
    ("scallion", "scallions"), ("scallion", "spring onions"),
    ("pepper", "peppers"), ("pepper", "mixed peppers"),
    ("lettuce", "lettuce"), ("lettuce", "lettuces"),
    ("marigold", "marigolds"), ("basil", "basil"),
]

# Observed in the field, not guessed. Kept OUT of the module-level HOMOPHONES:
# "water" appears in hard_bases, so extending that dict would re-roll the hard
# category and change 2120 existing cases.
OBSERVED_STT = {
    "water":     ["butter"],                 # heard live: "butter the tomatoes"
    "lettuce":   ["letters", "lattices"],    # heard repeatedly, two sessions
    "lettuces":  ["letters", "lattices"],
    "scallions": ["stallions"],
    "spearmint": ["spare mint"],
    "marigolds": ["mary golds"],
}

# Trailing Irish-English politeness, as observed ("...for us please").
DEMO_SUFFIX = ["for us please", "please", "would you", "if you wouldn't mind",
               "when you get a chance", "there's a good lad", "thanks a million"]
DEMO_PREFIX = ["would you ever", "could you", "can you", "sure would you",
               "be a pet and", "when you get a second"]

# Why an older gardener is asking the robot instead of doing it — abstracted
# from the demo's recurring themes (heavy cans, digging, getting back up).
DEMO_WHY = [
    "the watering can is too heavy for me now",
    "i can't be getting down on that ground",
    "it's easy enough to get down but not to get back up",
    "my hands aren't what they were",
    "i'm not able for the digging anymore",
    "the heat is at me today",
    "i've the hip playing up",
]

pool_demo = {}          # category -> list of pool items
demo_priority = set()

_st = random.getstate()
random.seed(90210)      # private stream; restored below so wave 1 is untouched


def _d(cat, sub, u, cls, cmds, desc, forb=None, prio=False):
    pool_demo.setdefault(cat, []).append((sub, u, cls, cmds, desc, forb))
    if prio:
        demo_priority.add(u)


def _stumble(word):
    """A real stumble lands ON the hard word. Returns variants of that word."""
    head = word[:3] if len(word) > 4 else word[:2]
    return [
        f"{head}- {word}",          # restart mid-word
        f"um {word}",
        f"{word}, {word}",          # say it twice, as observed
        f"the {word} i mean the {word}",
    ]


def _stt(u):
    """Apply an OBSERVED mishearing, if the utterance contains a known word."""
    words = u.split()
    idx = [i for i, w in enumerate(words) if w.lower().strip(",") in OBSERVED_STT]
    if not idx:
        return None
    i = random.choice(idx)
    key = words[i].lower().strip(",")
    words[i] = random.choice(OBSERVED_STT[key])
    return " ".join(words)


# ---- direct (55): what wave 1 does NOT already contain -----------------------
# Plain phrasings ("water the tomatoes") are wave 1's job and collide on dedupe,
# which is correct — this wave is only worth its slots where it adds something
# wave 1 cannot: the mishearings that actually happen, and stumbles that land on
# the plant name.
_direct_bases = []
for sp, alias in DEMO_SPECIES:
    _direct_bases += [
        (f"water the {alias}", [f"@move:{sp}", "D_W_1"]),
        (f"water all the {alias}", [f"@move:{sp}", "D_W_1"]),
        (f"give the {alias} a drink", [f"@move:{sp}"]),
        (f"move to the {alias}", ["M "]),
        (f"move over to the {alias}", ["M "]),
    ]

# Every observed mishearing, not a random one — these strings appear nowhere in
# wave 1, which is the point.
for u, cmds in _direct_bases:
    words = u.split()
    for i, w in enumerate(words):
        key = w.lower().strip(",")
        for wrong in OBSERVED_STT.get(key, []):
            v = words[:]
            v[i] = wrong
            _d("direct", "observed_stt", " ".join(v), "robot_command", cmds,
               "Mishearing observed at the demo", prio=True)

# Stumble on the species, as observed.
for sp, alias in DEMO_SPECIES:
    for v in _stumble(alias):
        _d("direct", "species_stumble", f"water the {v}", "robot_command",
           [f"@move:{sp}", "D_W_1"], "Stumble on the plant name")
        _d("direct", "species_stumble", f"move to the {v}", "robot_command",
           ["M "], "Stumble on the plant name")

# Trailing Irish politeness on a plain command.
for sp, alias in DEMO_SPECIES:
    _d("direct", "irish_politeness", f"water the {alias} for us please",
       "robot_command", [f"@move:{sp}", "D_W_1"], "Trailing politeness, as heard")
    _d("direct", "irish_politeness", f"move to the {alias} please",
       "robot_command", ["M "], "Move with please, as heard")

# The plain phrasings last: they will mostly collide with wave 1 and be skipped,
# which is the intended outcome.
for u, cmds in _direct_bases:
    _d("direct", "demo_plain", u, "robot_command", cmds, "Demo phrasing")

# ---- hard (115): the realism core ------------------------------------------
# Self-correction only where the name is GENUINELY ambiguous. The observed case
# was a speaker wavering between "lettuces" and "lettuce" — an awkward plural —
# then saying she didn't know what she'd called it. Smearing that template over
# every species produces "the basil, i don't know what i called them, the basil",
# which nobody says: no one is uncertain about basil. That would be the same
# mechanical transform this wave exists to correct. So: real alternations only.
NAME_WOBBLE = [
    ("lettuce", "lettuces", "lettuce"),
    ("lettuce", "lettuce", "lettuces"),
    ("lettuce", "little gem", "lettuce"),
    ("spearmint", "mint", "spearmint"),
    ("spearmint", "spearmint", "mint"),
    ("scallion", "spring onions", "scallions"),
    ("scallion", "scallions", "spring onions"),
    ("scallion", "green onions", "scallions"),
    ("tomato", "tomato plants", "tomatoes"),
    ("pepper", "mixed peppers", "peppers"),
    ("pepper", "peppers", "mixed peppers"),
    ("marigold", "marigolds", "marigold"),
]
for sp, said, meant in NAME_WOBBLE:
    _d("hard", "species_selfcorrect", f"water all the {said}, {meant} i mean",
       "robot_command", [f"@move:{sp}"], "Correct the plant name mid-sentence", prio=True)
    _d("hard", "species_selfcorrect",
       f"can you water the {said} i don't know what i called them, the {meant}",
       "robot_command", [f"@move:{sp}"], "Unsure what the plant is called", prio=True)
    _d("hard", "species_selfcorrect", f"the {said} or the {meant} whatever you call them, give them a drink",
       "robot_command", [f"@move:{sp}"], "Offers both names, unsure which")

for sp, alias in DEMO_SPECIES:
    # Retry after not being understood — no full stops; people just say it again.
    _d("hard", "retry", f"water the {alias}... water the {alias}",
       "robot_command", [f"@move:{sp}"], "Repeat after a non-understanding")
    # trailing Irish politeness
    for suf in random.sample(DEMO_SUFFIX, 3):
        _d("hard", "irish_politeness", f"water the {alias} {suf}",
           "robot_command", [f"@move:{sp}"], "Trailing politeness, as heard")
    for pre in random.sample(DEMO_PREFIX, 2):
        _d("hard", "irish_politeness", f"{pre} water the {alias}",
           "robot_command", [f"@move:{sp}"], "Leading politeness, as heard")
    # why-they're-asking, the demo's recurring theme
    for why in random.sample(DEMO_WHY, 2):
        _d("hard", "elderly_context", f"{why} so would you water the {alias}",
           "robot_command", [f"@move:{sp}"], "Reason for asking, as heard")
    # stumble inside a longer sentence
    _d("hard", "species_stumble", f"would you ever give the {_stumble(alias)[0]} a drink",
       "robot_command", [f"@move:{sp}"], "Stumble mid-sentence")
    s = _stt(f"water all the {alias} please")
    if s:
        _d("hard", "observed_stt", s, "robot_command", [f"@move:{sp}"],
           "Mishearing inside a polite request")

# ---- query (25) -------------------------------------------------------------
for sp, alias in DEMO_SPECIES:
    _d("query", "demo_plain", f"how are the {alias} getting on", "robot_query",
       ["D_S_C"], "Status, demo phrasing", prio=True)
    _d("query", "irish_politeness", f"would you check the {alias} for us please",
       "robot_query", ["D_S_C"], "Status with trailing politeness")
    _d("query", "demo_plain", f"is the ground dry at the {alias}", "robot_query",
       ["D_S_C"], "Soil question, demo phrasing")
    _d("query", "species_stumble", f"how are the {_stumble(alias)[0]} doing",
       "robot_query", ["D_S_C"], "Stumble in a status question")
    s = _stt(f"how are the {alias} looking")
    if s:
        _d("query", "observed_stt", s, "robot_query", ["D_S_C"],
           "Mishearing in a status question")

# ---- multi (20) -------------------------------------------------------------
for sp, alias in DEMO_SPECIES:
    _d("multi", "demo_chain", f"water the {alias} for us please and then go home",
       "robot_command", [f"@move:{sp}", "H_0"], "Chain, demo phrasing", prio=True)
    _d("multi", "demo_chain", f"check the {alias} and water them if they need it",
       "robot_command", ["D_S_C"], "Check then water")
    _d("multi", "demo_chain", f"would you water the {alias} and take a photo for us",
       "robot_command", [f"@move:{sp}", "I_1"], "Water then photo, demo phrasing")

# ---- refusal (40): plants the demo crowd actually named, none of them in gh1 -
# Plural, because that is how they were said ("I have cabbages, and I have
# onions"). Using the singular here produced "how are the cabbage getting on",
# which is not English and tests nothing about refusal.
DEMO_ABSENT = ["cabbages", "onions", "rhubarb", "runner beans", "spuds",
               "carrots", "courgettes", "strawberries", "garlic", "leeks"]
for p in DEMO_ABSENT:
    _d("refusal", "unknown_species", f"water the {p} for us please", "refusal", [],
       "Plant the demo crowd named, not in gh1", prio=True)
    _d("refusal", "unknown_species", f"would you ever water the {p}", "refusal", [],
       "Plant the demo crowd named, not in gh1")
for p in DEMO_ABSENT:
    _d("refusal", "unknown_species", f"the {p} could do with a drink", "refusal", [],
       "Indirect ask for an absent plant")
    _d("refusal", "unknown_species", f"move over to the {p}", "refusal", [],
       "Move to an absent plant")
    _d("refusal", "unknown_species", f"how are the {p} getting on", "refusal", [],
       "Status query on an absent plant")
    _d("refusal", "unknown_species", f"i've {p} in at home, would you water them",
       "refusal", [], "Talks about their own plants, not gh1's")

# ---- out_of_scope (45): what the demo crowd asked for that it CANNOT do -----
DEMO_IMPOSSIBLE = [
    "dig up the bed for me", "dig the garden over", "turn the soil for us",
    "put the clay in for me", "fertilise the soil", "put fertiliser on the beds",
    "spread the compost", "prune the tomato plants", "pick the tomatoes for me",
    "harvest the lettuce", "pull that big weed by the door", "mow the lawn",
    "clean the water head", "descale the nozzle", "fix the hose",
    "carry the watering can over", "lift that bag of compost",
    "plant these seeds i brought", "put up a trellis", "open the greenhouse door",
    "close the vents it's roasting", "put the heating on in here",
    "tell me the price of one of these", "order me another robot",
    "ring my daughter", "put the kettle on", "make us a cup of tea",
    "read out my messages", "put the radio on", "what's on the telly tonight",
]
for u in DEMO_IMPOSSIBLE:
    _d("out_of_scope", "demo_impossible", u, "out_of_scope", [],
       "Asked at the demo; the gantry cannot do it", prio=True)
for u in DEMO_IMPOSSIBLE:
    _d("out_of_scope", "demo_impossible", f"would you ever {u}", "out_of_scope", [],
       "Politely asked for the impossible")
for u in DEMO_IMPOSSIBLE[:15]:
    _d("out_of_scope", "demo_impossible", f"{u} for us please", "out_of_scope", [],
       "Impossible ask with trailing politeness")

# ---- general (35): what the demo crowd actually asked about -----------------
DEMO_QUESTIONS = [
    "how is the weather looking today", "will it rain later",
    "is it too cold to plant out yet", "what should i be planting this month",
    "when do i sow the cabbage", "do the tomatoes need feeding",
    "how often should i water in this heat", "is the soil any good in that end",
    "why are the marigolds struggling", "what grows well in a glass house",
    "can i grow spuds in a bag", "when do i lift the onions",
    "should i be watering in the evening", "do slugs go for lettuce",
    "is it worth putting fertiliser down", "how long do scallions take",
    "what do i do with the weeds after", "is rain water better for them",
    "will the frost get them", "do peppers need a lot of sun",
    "how do i keep the greenfly off", "when should i take the tomatoes in",
    "is it too late to sow basil", "do i need to feed the soil every year",
    "what's the best thing for a small glass house",
]
for u in DEMO_QUESTIONS:
    _d("general", "demo_advice", u, "general", [], "Asked at the demo", prio=True)
for u in DEMO_QUESTIONS:
    _d("general", "demo_advice", f"tell us this, {u}", "general", [],
       "Conversational opener, as heard")

# ---- negation (25) ----------------------------------------------------------
for sp, alias in DEMO_SPECIES:
    _d("negation", "plant_negation", f"don't water the {alias}, they're grand",
       "negation", [], "Irish 'grand' negation", [f"@move:{sp}", "D_W_1"], prio=True)
for sp, alias in DEMO_SPECIES:
    _d("negation", "plant_negation", f"leave the {alias} alone for today",
       "negation", [], "Leave-alone negation", [f"@move:{sp}", "D_W_1"])
    _d("negation", "plant_negation", f"the {alias} are grand, don't be watering them",
       "negation", [], "Irish 'grand' negation", [f"@move:{sp}", "D_W_1"])
for u in ["no no leave it", "don't bother, sure it's after raining",
          "ah leave it now", "no need, they're only just done",
          "hold off there a minute", "you're grand, leave it",
          "don't be at that now"]:
    _d("negation", "blanket_negation", u, "negation", [],
       "Blanket negation, demo phrasing", ["D_W_1", "M "], prio=True)

# ---- tool (20): natural tool phrasings + heads it hasn't --------------------
for u, t in [("put the water head on for us", "watering_nozzle"),
             ("get the water tool head", "watering_nozzle"),
             ("would you ever put the spray head on", "watering_nozzle"),
             ("pop the soil sensor on", "soil_sensor"),
             ("get the probe out for us please", "soil_sensor"),
             ("put the weeding head on", "weeder"),
             ("get the seeding head", "seeder")]:
    bay = {"watering_nozzle": 2, "soil_sensor": 1, "weeder": 4, "seeder": 3}[t]
    _d("tool", "demo_plain", u, "robot_command", [f"T_{bay}_1"],
       "Tool head, demo phrasing", prio=True)
for u in ["take that head off for us please", "put the head back would you",
          "that's grand you can put it away now", "pop the tool back in"]:
    _d("tool", "demo_plain", u, "robot_command", ["T_2_2"],
       "Stow, demo phrasing", prio=True)
for u in ["put the digging head on", "get the fertiliser head",
          "put the pruning head on", "get the harvesting head",
          "put the mowing head on", "get the spade head",
          "put the fork attachment on", "get the hedge cutting head"]:
    _d("tool", "unknown_tool", u, "refusal", [],
       "A head this robot doesn't have", ["T_"], prio=True)
for u, t in [("get the sprayer for us", "watering_nozzle"),
             ("put on the watering head please", "watering_nozzle"),
             ("would you put the soil probe on", "soil_sensor"),
             ("get the weeder out", "weeder"),
             ("put the seeder on for us please", "seeder")]:
    bay = {"watering_nozzle": 2, "soil_sensor": 1, "weeder": 4, "seeder": 3}[t]
    _d("tool", "irish_politeness", u, "robot_command", [f"T_{bay}_1"],
       "Tool head with demo politeness")
for u in ["take it off there for us", "put that back where it came from",
          "you can drop that head now", "stow it away please"]:
    _d("tool", "demo_plain", u, "robot_command", ["T_2_2"], "Stow, demo phrasing")

random.setstate(_st)   # wave 1's stream is exactly where it was

DEMO_TARGET = {"direct": 55, "hard": 115, "query": 25, "multi": 20,
               "refusal": 40, "out_of_scope": 45, "general": 35,
               "negation": 25, "tool": 20}
_DEMO_DIFF = {"direct": "easy", "hard": "hard", "query": "easy", "multi": "medium",
              "refusal": "medium", "out_of_scope": "medium", "general": "easy",
              "negation": "hard", "tool": "medium"}
assert sum(DEMO_TARGET.values()) == 380, sum(DEMO_TARGET.values())

# NOTE: the wave is added to the corpus AFTER the assembly below, not here.
# Adding it first looked equivalent — raise each cap by the number seeded and
# `need` stays the same — but it isn't: C.add() claims the utterance in the
# dedupe set, so a demo phrasing that collides with one wave 1 would generate
# STEALS it, and the assembly silently draws a different case in its place.
# Measured: seeding "can you water the lettuce" here dropped that case from
# wave 1 and shifted every id after it. Appending afterwards resolves every
# collision in wave 1's favour, which is the direction that keeps 2120 ids
# stable.

# ------------------------------------------------------------ assembly ------

# "tool" is appended LAST on purpose. CAT_ORDER derives from these keys and ids
# are assigned in that order, so a new category at the end takes GM-2001+ and
# every original id stays put. Inserting it anywhere else would renumber the
# corpus and orphan every GM- reference in the eval record.
# Wave 1's caps, UNCHANGED. The demo wave is appended after this assembly runs,
# so these must stay exactly as they were or the draws below shift.
CAPS = {
    "direct": 380, "indirect": 200, "query": 180, "general": 190,
    "emergency": 40, "multi": 140, "safety": 15, "refusal": 175,
    "hard": 340, "negation": 100, "out_of_scope": 240,
    "tool": 120,
}
assert sum(CAPS.values()) == 2120, sum(CAPS.values())

POOLS = {
    "direct": pool_direct, "indirect": pool_indirect, "query": pool_query,
    "general": pool_general, "emergency": pool_emergency, "multi": pool_multi,
    "safety": pool_safety, "refusal": pool_refusal, "hard": pool_hard,
    "negation": pool_negation, "out_of_scope": pool_oos, "tool": pool_tool,
}

DIFFICULTY = {
    "direct": "easy", "indirect": "medium", "query": "easy", "general": "easy",
    "emergency": "easy", "multi": "medium", "safety": "medium",
    "refusal": "medium", "hard": "hard", "negation": "hard",
    "out_of_scope": "medium", "tool": "medium",
}

# Items that must survive sampling: hand-written hard cases, blanket negations,
# and the tool baseline (one mount per head, some plain stows, every refusal).
PRIORITY = {
    "hard": set(h[1] for h in HARD_HAND),
    "negation": set(u for (sub, u, *_rest) in pool_negation if sub == "blanket_negation"),
    "tool": tool_priority,
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

# ---- wave 2 lands here: after wave 1 is complete and its utterances claimed --
# Any demo phrasing that collides with a wave 1 case is skipped (C.add returns
# False) and the next candidate is taken, so wave 1 is untouched by
# construction rather than by luck. That is why the pools above carry headroom.
demo_added = {}
for cat, want in DEMO_TARGET.items():
    items = pool_demo.get(cat, [])
    items.sort(key=lambda it: (it[1] not in demo_priority, it[1]))  # priority first
    n = 0
    for sub, u, cls, cmds, desc, forb in items:
        if n >= want:
            break
        if C.add(cat, sub, _DEMO_DIFF[cat], u, cls, cmds, desc,
                 forbidden=forb, source="demo_observed"):
            n += 1
    demo_added[cat] = n
    if n < want:
        raise SystemExit(
            f"demo pool exhausted for {cat}: wanted {want}, got {n} "
            f"(pool {len(items)}) — add more phrasings, do not lower the target")

# Stable ordering, then ids. The FIRST key is the wave: everything from wave 1
# sorts ahead of everything from wave 2, so the original 2120 keep GM-0001..
# GM-2120 and the demo-derived cases take GM-2121..GM-2500. Sorting by category
# first instead would interleave the new cases by utterance and renumber the
# whole corpus, orphaning every GM- reference in the eval record and making old
# result JSONLs unjoinable.
CAT_ORDER = list(CAPS.keys())
C.cases.sort(key=lambda c: (c["source"] == "demo_observed",
                            CAT_ORDER.index(c["category"]),
                            c["source"] != "seed",
                            c["utterance"]))
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
        "demo_observed_cases": sum(1 for c in C.cases if c["source"] == "demo_observed"),
        "waves": {
            "wave_1": "GM-0001..GM-2120 — 43 hand-written seeds plus generated "
                      "transforms (seed 42).",
            "wave_2": "GM-2121..GM-2500 — 380 cases written against speech "
                      "patterns observed at the 2026-06/07 community "
                      "demonstrations. Patterns only: no participant name, "
                      "personal circumstance, or verbatim personal remark is "
                      "reproduced (the transcripts are personal data and are "
                      "not in this repository; this file is public).",
        },
        "wave_2_rationale": (
            "Wave 1's disfluency is mechanical — a random word is duplicated, a "
            "filler is inserted at a random index, and the homophones are "
            "invented. The demos showed all three are wrong in the same "
            "direction: the STT actually renders 'water' as 'butter' and "
            "'lettuce' as 'letters'/'lattices' (none of which occur in wave 1), "
            "and speakers stumble on the plant name specifically, not on a "
            "random word. Wave 2 supplies the observed errors and puts the "
            "disfluency where it actually falls."
        ),
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
            "T_<n>_1 / T_<n>_2": "tool bay <n> mounted / released; bays are from gh1.yaml",
            "tool category precondition": (
                "stow cases (expected T_2_2) assume the watering nozzle is on, and "
                "mount cases assume an empty UTM. ToolState is process state that "
                "persists across cases, so the harness MUST park it via POST "
                "/tool_state before each tool case or the expected command is "
                "whatever the previous case happened to leave mounted."
            ),
        },
    },
    "cases": C.cases,
}

# encoding is explicit: ensure_ascii=False means non-ASCII reaches the file, and
# without this Python uses the platform default (cp1252 on Windows) and writes
# mojibake that json.load then refuses. Latent until wave 2's metadata became
# the first non-ASCII in the corpus. load_corpus.py reads utf-8 to match.
with open(str(__import__("pathlib").Path(__file__).parent / "growmate_test_corpus.json"),
          "w", encoding="utf-8") as f:
    json.dump(corpus, f, indent=2, ensure_ascii=False)

print(f"total: {len(C.cases)}")
for k in CAT_ORDER:
    print(f"  {k:14s} {counts[k]}")
print("unique utterances:", len({c['utterance'].lower() for c in C.cases}))
