"""Greedy reference outputs: record them from one build, compare another build.

usage: greedy.py record OUT.json
       greedy.py compare REFERENCE.json [OUT.json]

Temperature 0, reasoning off, 128 tokens per prompt. The API returns text, so
token IDs are the text re-encoded with the model's tokenizer; the first
differing ID locates a divergence.
"""
import json, sys
from pathlib import Path
import common

MAX_TOKENS = 128
PROMPTS = {
    "prose-continue": "Continue this story for a few paragraphs: The harbour lights went out one "
                      "by one, and Mara realised the ferry was not coming back.",
    "prose-explain": "Explain to a curious twelve-year-old why the sky is blue and sunsets are red.",
    "code-write": "Write a Python function that merges overlapping intervals in a list of "
                  "(start, end) tuples. Include a short docstring and two doctest examples.",
    "code-review": "What does this C++ do, and is there a bug?\n\n"
                   "```cpp\nint sum(const std::vector<int>& v) {\n  int s = 0;\n"
                   "  for (size_t i = 0; i <= v.size(); ++i) s += v[i];\n  return s;\n}\n```",
    "math": "A train leaves at 09:40 and travels 214 km at an average of 86 km/h. "
            "When does it arrive? Show the calculation.",
    "german": "Übersetze ins Englische: Die Besprechung wurde auf Donnerstag verschoben, "
              "weil zwei Kolleginnen krank sind.",
    # ~2k tokens of fixed prose: exercises chunked prefill and long-row attention.
    "prose-2k-summary": None,
}


def prompt_text(name):
    text = PROMPTS[name]
    if text is None:
        text = common.prose_corpus()[:8400] + "\n\nSummarize the passage above in five sentences."
    return text


def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(common.REPO / "install/models" / common.MODEL / "tokenizer")


def run():
    tok = tokenizer()
    results = {}
    for name in PROMPTS:
        text, timings = common.chat([{"role": "user", "content": prompt_text(name)}], MAX_TOKENS)
        results[name] = {"text": text, "ids": tok.encode(text, add_special_tokens=False),
                         "prompt_n": timings["prompt_n"], "predicted_n": timings["predicted_n"]}
        print(f"{name}: {timings['predicted_n']} tokens", file=sys.stderr, flush=True)
    return results


def compare(reference, current):
    ok = True
    for name, ref in reference.items():
        cur = current.get(name)
        if cur is None:
            print(f"MISSING  {name}")
            ok = False
            continue
        if cur["text"] == ref["text"]:
            print(f"SAME     {name}")
            continue
        ok = False
        a, b = ref["ids"], cur["ids"]
        i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
        print(f"DIFFERS  {name}: first differing token {i} of {len(a)}/{len(b)}")
        shared = common_prefix(ref["text"], cur["text"])
        print(f"  shared text ends:    {shared[-80:]!r}")
        print(f"  reference continues: {ref['text'][len(shared):][:120]!r}")
        print(f"  current continues:   {cur['text'][len(shared):][:120]!r}")
    return ok


def common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "record":
        Path(sys.argv[2]).write_text(json.dumps(run(), ensure_ascii=False, indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == "compare":
        reference = json.loads(Path(sys.argv[2]).read_text())
        current = run()
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text(json.dumps(current, ensure_ascii=False, indent=1))
        sys.exit(0 if compare(reference, current) else 1)
    else:
        sys.exit(__doc__)
