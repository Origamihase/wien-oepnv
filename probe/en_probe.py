"""One-off EN probe (not part of the product; removed before merge).

Runs the EN pipeline of the code under ROOT with the real model over every
distinct DE text in INPUT (shard K of N) and writes field-level English plus
every model call (input -> output). Model calls are memoised in MEMO so a
second code root re-uses identical model inputs.
"""
from __future__ import annotations

import json
import os
import sys

root, inp, out, memo_path, shard, nshards = sys.argv[1:7]
inp, out, memo_path = (os.path.abspath(p) for p in (inp, out, memo_path))
root = os.path.abspath(root)
os.chdir(root)  # the code validates its paths against the working directory
sys.path.insert(0, root)
import src.build_feed as bf  # noqa: E402

try:
    memo = json.load(open(memo_path, encoding="utf-8"))
except FileNotFoundError:
    memo = {}
calls: dict[str, str] = {}
real = bf._get_translation_pipeline()
assert real is not None, "model did not load"


def model(text, **kw):
    if text not in memo:
        memo[text] = real(text, **kw)[0]["translation_text"]
    calls[text] = memo[text]
    return [{"translation_text": memo[text]}]


bf._TRANSLATION_STATE["pipeline"] = model
items = json.load(open(inp, encoding="utf-8"))
k, n = int(shard), int(nshards)
res = {}
for i, it in enumerate(items):
    if i % n != k:
        continue
    calls.clear()
    if it["field"] == "title":
        raw = bf._translate_title_attempt(it["de"], "probe", source=it["source"])
        en = None if raw is None else bf._capitalise_glossary_after_stop(
            bf._capitalise_title_body(bf._drop_unpaired_quote(
                bf._WHITESPACE_RE.sub(" ", bf._sanitize_text(raw)).strip())))
    else:
        raw = bf._translate_text_attempt(it["de"], "probe", source=it["source"])
        en = None if raw is None else bf._capitalise_glossary_after_stop(
            bf._capitalise_sentence_start(bf._drop_unpaired_quote(
                bf._sanitize_text(raw))))
    res[it["key"]] = {"en": en, "calls": dict(calls)}
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
json.dump(memo, open(memo_path, "w", encoding="utf-8"), ensure_ascii=False)
print(len(res), "texts,", len(memo), "memoised model inputs")
