"""Prefill and decode throughput, and draft acceptance at depth.

usage: bench.py run OUT.jsonl MODE:TOKENS [MODE:TOKENS ...]    (MODE prose|code)
       bench.py accept OUT.jsonl MODE:TOKENS
       bench.py depth OUT.jsonl MODE:TOKENS [REPEATS]
       bench.py compare REFERENCE_DIR CURRENT_DIR [--tolerance 0.05]

run: one uncached prompt per size. Each prompt starts with a tag distinct
within the run, so no prefix cache applies on a fresh server, and every build
sees byte-identical prompts (a random nonce would change the output, and with
it the draft acceptance).

accept: eight different questions of one cached context. Their mean decode
rate, acceptance and time per verify step judge decode at depth, where one
prompt's rate is one continuation's acceptance.

depth: one prompt prefilled, then repeated from the cache.

compare: fails when prefill (every size), decode (below 16K) or the accept
decode mean falls more than the tolerance below the reference, or the time
per verify step rises more than the tolerance above it. Timings are the
server's; acceptance is the share of drafted tokens accepted.
"""
import sys
import common

OUT_TOKENS = 256
ASK = {"prose": "Summarize the passage above in detail.",
       "code": "Explain how the code above plans and dispatches kernels, quoting the relevant code."}


def prompt(mode, tokens, corpus, tag):
    text = corpus[: int(tokens * common.CHARS_PER_TOKEN[mode])]
    return f"[{tag}]\n\n{text}\n\n{ASK[mode]}"


def drafts():
    m = common.status().get("metrics", {})
    return m.get("drafted_tokens", 0), m.get("accepted_draft_tokens", 0)


def measure(messages):
    """One request with its server timings and its draft acceptance."""
    d0, a0 = drafts()
    _, t = common.chat(messages, OUT_TOKENS)
    d1, a1 = drafts()
    steps = t["predicted_n"] - (a1 - a0)  # each verify step emits one token plus its accepted drafts
    return {"prompt_n": t["prompt_n"], "cache_n": t["cache_n"],
            "prefill": round(t["prompt_per_second"], 2), "ttft": round(t["prompt_ms"] / 1e3, 2),
            "gen_n": t["predicted_n"], "decode": round(t["predicted_per_second"], 2),
            "drafted": d1 - d0, "accepted": a1 - a0,
            "acceptance": round((a1 - a0) / (d1 - d0), 3) if d1 > d0 else None,
            "ms_per_step": round(t["predicted_ms"] / steps, 1) if steps > 0 else None}


def run(out, specs):
    corpora = {}
    for i, spec in enumerate(specs):
        mode, _, size = spec.partition(":")
        if mode not in corpora:
            corpora[mode] = common.prose_corpus() if mode == "prose" else common.code_corpus()
        messages = [{"role": "user", "content": prompt(mode, int(size), corpora[mode], f"bench {i} {spec}")}]
        common.emit({"mode": mode, "target": int(size), **measure(messages)}, out)


def depth(out, mode, size, repeats):
    """Decode at depth: one cold prefill, then cached repeats of the same prompt."""
    corpus = common.prose_corpus() if mode == "prose" else common.code_corpus()
    messages = [{"role": "user", "content": prompt(mode, size, corpus, f"depth {mode}:{size}")}]
    for i in range(repeats + 1):
        common.emit({"depth": f"{mode}:{size}", "run": i, **measure(messages)}, out)


QUESTIONS = (
    "Summarize the passage above in detail.",
    "List every named character above with one sentence about each.",
    "Describe the prose style of the passage above, quoting three short examples.",
    "Write the next scene of the story in the same style.",
    "Explain the invented technology and slang used above, term by term.",
    "Give a numbered timeline of the events in the passage above.",
    "Rewrite the opening of the passage above as a screenplay.",
    "What themes does the passage above develop? Argue for the two strongest.",
)


def accept(out, mode, size):
    """Draft acceptance at depth: one cached context, several different questions.

    Each question after the first reuses the context from the cache and yields
    an independent continuation, so the mean acceptance is not one text's."""
    corpus = common.prose_corpus() if mode == "prose" else common.code_corpus()
    text = corpus[: int(size * common.CHARS_PER_TOKEN[mode])]
    rows = []
    for i, question in enumerate(QUESTIONS):
        messages = [{"role": "user", "content": f"[accept {mode}:{size}]\n\n{text}\n\n{question}"}]
        row = {"accept": f"{mode}:{size}", "question": i, **measure(messages)}
        common.emit(row, out)
        rows.append(row)
    drafted = sum(r["drafted"] for r in rows)
    accepted = sum(r["accepted"] for r in rows)
    steps = sum(r["gen_n"] - r["accepted"] for r in rows)
    common.emit({"accept": f"{mode}:{size}", "summary": True, "questions": len(rows),
                 "acceptance": round(accepted / drafted, 3) if drafted else None,
                 "tokens_per_step": round(sum(r["gen_n"] for r in rows) / steps, 3),
                 "decode_mean": round(sum(r["decode"] for r in rows) / len(rows), 2)}, out)


# One long prompt's decode rate is one continuation's draft acceptance: on an
# M1 Max at 32K it moves by +-10% between questions of the same context, so a
# single sample cannot resolve a 5% change. Decode at depth is judged on the
# accept mean and on the time per verify step, which acceptance does not move.
SINGLE_SAMPLE_DECODE_TOKENS = 16000


def rows(path, kind):
    import json
    try:
        return [r for r in map(json.loads, open(path)) if kind in r]
    except FileNotFoundError:
        return []


def compare(reference, current, tolerance):
    """Compare REFERENCE_DIR/{bench,accept}.jsonl with CURRENT_DIR's."""
    ok = True

    def check(label, before, after, gate=True, lower_is_better=False):
        nonlocal ok
        ratio = after / before
        worse = gate and (ratio > 1 + tolerance if lower_is_better else ratio < 1 - tolerance)
        ok &= not worse
        print(f"{'WORSE' if worse else 'ok   ' if gate else 'info '} {label}: "
              f"{before:.1f} -> {after:.1f} ({ratio - 1:+.1%})")

    base = {(r["mode"], r["target"]): r for r in rows(f"{reference}/bench.jsonl", "mode")}
    for c in rows(f"{current}/bench.jsonl", "mode"):
        b = base.get((c["mode"], c["target"]))
        if not b:
            print(f"      {c['mode']}:{c['target']} no reference")
            continue
        label = f"{c['mode']}:{c['target']}"
        check(f"{label} prefill", b["prefill"], c["prefill"])
        check(f"{label} decode", b["decode"], c["decode"], gate=c["target"] < SINGLE_SAMPLE_DECODE_TOKENS)
    base = {r["accept"]: r for r in rows(f"{reference}/accept.jsonl", "summary")}
    for c in rows(f"{current}/accept.jsonl", "summary"):
        b = base.get(c["accept"])
        if not b:
            print(f"      accept {c['accept']} no reference")
            continue
        check(f"accept {c['accept']} decode mean", b["decode_mean"], c["decode_mean"])
        check(f"accept {c['accept']} tokens per step", b["tokens_per_step"], c["tokens_per_step"], gate=False)
    steps = {name: [r["ms_per_step"] for r in rows(f"{d}/accept.jsonl", "question")[1:] if r["ms_per_step"]]
             for name, d in (("reference", reference), ("current", current))}
    if steps["reference"] and steps["current"]:
        mean = {k: sum(v) / len(v) for k, v in steps.items()}
        check("accept ms per verify step", mean["reference"], mean["current"], lower_is_better=True)
    return ok


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) >= 4 and sys.argv[1] == "accept":
        mode, _, size = sys.argv[3].partition(":")
        accept(sys.argv[2], mode, int(size))
    elif len(sys.argv) >= 4 and sys.argv[1] == "depth":
        mode, _, size = sys.argv[3].partition(":")
        depth(sys.argv[2], mode, int(size), int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        tolerance = float(sys.argv[sys.argv.index("--tolerance") + 1]) if "--tolerance" in sys.argv else 0.05
        sys.exit(0 if compare(sys.argv[2], sys.argv[3], tolerance) else 1)
    else:
        sys.exit(__doc__)
