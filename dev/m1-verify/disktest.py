"""SSD cache tier (--max-cache-disk): an evicted prefix comes back from disk.

usage: disktest.py OUT.jsonl [TOKENS]

Run against a server started with --max-cache-disk and a --max-memory small
enough that RAM cannot keep several TOKENS-long prompts. X is prefilled, then
unrelated prompts of the same length follow until /status reports demoted
KV pages; repeating X must then restore pages from disk, hit the cache and
produce X's output again. Exits 1 on any failure.
"""
import sys, uuid
import common

MAX_FILLERS = 8


def disk():
    return common.status()["disk"]


def main(out, tokens):
    corpus = common.prose_corpus()
    size = int(tokens * 4.2)
    if disk()["capacity_bytes"] == 0:
        common.emit({"check": "server has a disk tier", "ok": False}, out)
        return False

    def ask(i):
        return [{"role": "user", "content": f"[disk {uuid.uuid4() if i else nonce}]\n\n"
                                             f"{corpus[i * size:(i + 1) * size]}\n\nSummarize briefly."}]

    nonce = uuid.uuid4()
    x = ask(0)
    first, t = common.chat(x, 64)
    common.emit({"step": "X cold", "prompt_n": t["prompt_n"], "cache_n": t["cache_n"], **disk()}, out)
    for i in range(1, MAX_FILLERS + 1):
        _, t = common.chat(ask(i), 16)
        d = disk()
        common.emit({"step": f"filler {i}", "prompt_n": t["prompt_n"], "cache_n": t["cache_n"], **d}, out)
        if d["kv_demotions"] > 0:
            break
    before = disk()
    again, t = common.chat(x, 64)
    after = disk()
    common.emit({"step": "X repeat", "prompt_n": t["prompt_n"], "cache_n": t["cache_n"],
                 "ttft_s": round(t["prompt_ms"] / 1e3, 2), **after}, out)
    checks = {
        "pages were demoted to disk": before["kv_demotions"] > 0,
        "X restored pages from disk": after["kv_restores"] > before["kv_restores"],
        "X reused most of its prompt": t["cache_n"] >= t["prompt_n"] // 2,
        "no disk failures": after["kv_demotion_failures"] == 0 and after["kv_restore_failures"] == 0,
        "X output identical after restore": first == again,
    }
    for name, ok in checks.items():
        common.emit({"check": name, "ok": ok}, out)
    return all(checks.values())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 16000) else 1)
