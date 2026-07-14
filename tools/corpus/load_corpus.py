#!/usr/bin/env python3
"""
Loader: convert growmate_test_corpus.json into the (utterance, expected_class,
expected_commands, name, category) tuples the existing harness uses.

Notes on class mapping: the corpus uses two labels the original 43-case suite
didn't have, "negation" and "out_of_scope". By default they're passed through
unchanged; if your classifier folds them into "refusal", set FOLD_INTO_REFUSAL
to True. The corpus's "forbidden_commands" field (negation and self-correction
cases) is exposed separately via load_forbidden() since the 5-tuple can't
carry it: for those cases, assert the emitted sequence contains NONE of them.
"""

import json

FOLD_INTO_REFUSAL = False

def load_cases(path="growmate_test_corpus.json", categories=None):
    with open(path) as f:
        corpus = json.load(f)
    out = []
    for c in corpus["cases"]:
        if categories and c["category"] not in categories:
            continue
        cls = c["expected_class"]
        if FOLD_INTO_REFUSAL and cls in ("negation", "out_of_scope"):
            cls = "refusal"
        out.append((c["utterance"], cls, c["expected_commands"],
                    f'{c["id"]} {c["description"]}', c["category"]))
    return out

def load_forbidden(path="growmate_test_corpus.json"):
    """utterance -> list of command fragments that must NOT be emitted."""
    with open(path) as f:
        corpus = json.load(f)
    return {c["utterance"]: c["forbidden_commands"]
            for c in corpus["cases"] if c["forbidden_commands"]}

if __name__ == "__main__":
    cases = load_cases()
    print(f"{len(cases)} cases loaded")
    print("example:", cases[0])
    forb = load_forbidden()
    print(f"{len(forb)} cases carry forbidden_commands")
