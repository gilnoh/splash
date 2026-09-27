# M1/M2 release check

Run before adopting a new upstream or community-port release on an M1/M2 Mac. It
builds, runs `make test-engine`, starts a server and checks, in order:

| Check | Pass condition |
|---|---|
| kernel policy | the server logs `Kernel policy for GPU family 7` (`SPLASH_VERIFY_FAMILY`) |
| greedy outputs | 7 fixed prompts, temperature 0, reasoning off, 128 tokens: same text as the reference |
| benchmark | prefill at ~2k and ~32k (prose, code) and decode at ~2k within 5% of the reference; decode at 32k judged on the mean of eight questions of one cached context, and the time per verify step |
| prefix cache | `cachetest.py`: repeats and appended turns reuse the cache, an early edit does not, the cached output matches the cold one |
| SSD tier | `disktest.py`, with `--max-cache-disk` and a capped `--max-memory`: an evicted prefix is restored from disk with the same output |
| long context (`--long`) | one ~110K prompt prefills without the engine failing |

Record a reference on the known-good build, then compare the candidate:

```sh
# known-good build (another checkout or worktree), before changing anything
dev/m1-verify/verify.sh --root ~/progs/splash --record --out /tmp/verify-good
# candidate
dev/m1-verify/verify.sh --reference /tmp/verify-good --out /tmp/verify-new --long
```

Results land in `--out`: `summary.txt`, one log per step, and the raw JSON. A
greedy difference is not automatically a regression: upstream numerics changes
can move a greedy path. Find the first differing token (`greedy.txt`) and the
change responsible before using the build.

A single long prompt's decode rate is not a measurement of the build: it
depends on that continuation's draft acceptance, which moved decode by ±10%
between questions of the same 32k context on an M1 Max. Its 32k decode is
reported (`info`) but does not gate. Time per verify step does not depend on
acceptance; it separates slower kernels from a different continuation.

The prose corpus is any directory of `<book>/chapter-*.md` files
(`SPLASH_VERIFY_PROSE`); the code corpus is `runtime/` at tag `1.0.2`, so every
build sees the same text. Stop other GPU work before running: only one Splash
server at a time, and loaded Ollama models are stopped automatically.
