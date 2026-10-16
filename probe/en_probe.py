"""One-off EN probe (not part of the product; removed before merge).

Runs the EN pipeline of the code under ROOT with the real model over INPUT:
once as the build does, then REPEATS times with the short-placeholder pass
forced to fail, so the nonce pass runs under REPEATS fresh nonces.
"""
from __future__ import annotations

import json
import os
import re
import sys

root, inp, out, repeats = sys.argv[1:5]
inp, out = os.path.abspath(inp), os.path.abspath(out)
root = os.path.abspath(root)
os.chdir(root)
sys.path.insert(0, root)
import src.build_feed as bf  # noqa: E402

real = bf._get_translation_pipeline()
if real is None:
    raise SystemExit("model did not load")
SHORT = re.compile(r"X(?:ENT|GLO)\d+X")
state = {"force": False, "calls": []}


def model(text, **kw):
    if state["force"] and SHORT.search(text) and not re.search(r"X(?:ENT|GLO)[0-9a-f]{8,}X", text):
        state["calls"].append([text, "<forced fail>"])
        return [{"translation_text": "XENT99X"}]
    res = real(text, **kw)[0]["translation_text"]
    state["calls"].append([text, res])
    return [{"translation_text": res}]


bf._TRANSLATION_STATE["pipeline"] = model


def run(it):
    raw = bf._translate_text_attempt(it["de"], "probe", source=it["source"])
    return None if raw is None else bf._capitalise_glossary_after_stop(
        bf._capitalise_sentence_start(bf._drop_unpaired_quote(bf._sanitize_text(raw))))


res = {}
for it in json.load(open(inp, encoding="utf-8")):
    state["force"], state["calls"] = False, []
    entry = {"de": it["de"], "build": run(it), "build_calls": state["calls"], "nonce": []}
    state["force"] = True
    for _ in range(int(repeats)):
        state["calls"] = []
        entry["nonce"].append({"en": run(it), "calls": state["calls"]})
    res[it["key"]] = entry
    print(it["key"][:40], "|", entry["build"], flush=True)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
