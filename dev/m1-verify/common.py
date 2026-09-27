"""Shared helpers for the M1/M2 fork's release checks (see README.md here)."""
import glob, json, os, subprocess, time, urllib.request
from pathlib import Path

MODEL = os.environ.get("SPLASH_VERIFY_MODEL", "incoai/Qwen3.8-27B-Splash")
URL = os.environ.get("SPLASH_VERIFY_URL", "http://127.0.0.1:8011")
REPO = Path(__file__).resolve().parents[2]
# Prose corpus: any directory of chapter-*.md files, read in sorted order.
PROSE_DIR = Path(os.environ.get(
    "SPLASH_VERIFY_PROSE", "~/progs/ebook-scribe-with-intelligence/work")).expanduser()
PROSE_BOOKS = ("neuromancer", "count-zero", "mona-lisa-overdrive")
# Code corpus: the runtime sources of a fixed tag, so every build is timed on
# the same text whatever branch is checked out.
CODE_REF = os.environ.get("SPLASH_VERIFY_CODE_REF", "1.0.2")
CHARS_PER_TOKEN = {"prose": 4.2, "code": 3.4}


def prose_corpus():
    paths = [p for book in PROSE_BOOKS for p in sorted(glob.glob(str(PROSE_DIR / book / "chapter-*.md")))]
    if not paths:
        raise SystemExit(f"no prose corpus under {PROSE_DIR}; set SPLASH_VERIFY_PROSE")
    return "".join(open(p).read() for p in paths)


def code_corpus():
    names = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", CODE_REF, "runtime"],
                           check=True, capture_output=True, text=True).stdout.split()
    names = sorted(n for n in names if n.endswith((".cpp", ".hpp")))
    return "".join(
        f"// ===== {n}\n" + subprocess.run(["git", "-C", str(REPO), "show", f"{CODE_REF}:{n}"],
                                         check=True, capture_output=True, text=True).stdout
        for n in names)


def chat(messages, max_tokens, timeout=7200):
    """Greedy, reasoning-off chat completion; returns (text, timings)."""
    body = {"model": MODEL, "stream": False, "temperature": 0, "reasoning_effort": "none",
            "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"], data["timings"]


def status():
    with urllib.request.urlopen(URL + "/status", timeout=30) as r:
        return json.load(r)


def wait_ready(timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(URL + "/ready", timeout=5) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(5)
    raise SystemExit(f"server at {URL} not ready after {timeout} s")


def emit(record, out=None):
    line = json.dumps(record)
    print(line, flush=True)
    if out:
        with open(out, "a") as f:
            f.write(line + "\n")
