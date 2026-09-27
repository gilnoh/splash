"""Prefix-cache behaviour and correctness for an agent-style growing conversation.

usage: cachetest.py OUT.jsonl [BASE_TOKENS]

A: cold base prompt. B: exact repeat (must hit the cache, output identical to
A). C: append the reply and a ~4k tool output (only the new tail prefilled).
D: an unrelated short request. E: next turn (D must not have evicted it).
F: edit at the start (must not reuse the cache). Exits 1 on any failure.
"""
import sys, uuid
import common

PAGE = 32  # KV page tokens: the last partial page is always prefilled again


def main(out, base_tokens):
    corpus = common.prose_corpus()
    cut = int(base_tokens * 4.2)
    base_text, append_text = corpus[:cut], corpus[cut: cut + int(4000 * 4.2)]
    nonce = uuid.uuid4()
    failures = []

    def call(step, messages, max_tokens=64):
        text, t = common.chat(messages, max_tokens)
        common.emit({"step": step, "prompt_n": t["prompt_n"], "cache_n": t["cache_n"],
                     "prefilled": t["prompt_n"] - t["cache_n"], "ttft_s": round(t["prompt_ms"] / 1e3, 2)}, out)
        return text, t

    def check(name, ok):
        common.emit({"check": name, "ok": bool(ok)}, out)
        if not ok:
            failures.append(name)

    u1 = {"role": "user", "content": f"[session {nonce}]\n\n{base_text}\n\nSummarize the passage above briefly."}
    a_cold, ta = call("A cold", [u1])
    a_repeat, tb = call("B exact repeat", [u1])
    check("B reuses all but the last page", tb["cache_n"] >= tb["prompt_n"] - 2 * PAGE)
    check("B output identical to A", a_cold == a_repeat)

    turn2 = [u1, {"role": "assistant", "content": a_cold},
             {"role": "user", "content": f"Tool output (OCR):\n\n{append_text}\n\nWhat does the new text add?"}]
    a2, tc = call("C append 4k tool output", turn2)
    check("C reuses the base prompt", tc["cache_n"] >= ta["prompt_n"] - 2 * PAGE)

    call("D unrelated request", [{"role": "user", "content": "Name three primary colors."}], max_tokens=16)

    turn3 = turn2 + [{"role": "assistant", "content": a2},
                     {"role": "user", "content": "Tool output (web):\n\nThe Sprawl trilogy was published "
                                                 "1984-1988.\n\nComment briefly."}]
    _, te = call("E turn 3 after unrelated request", turn3)
    check("E reuses turn 2", te["cache_n"] >= tc["prompt_n"] - 2 * PAGE)

    edited = {"role": "user", "content": f"[session {nonce}]\n\nEDITED EARLY. {base_text}\n\n"
                                         "Summarize the passage above briefly."}
    _, tf = call("F edit near start", [edited])
    check("F does not reuse the edited prefix", tf["cache_n"] < 4 * PAGE)
    return not failures


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 32000) else 1)
