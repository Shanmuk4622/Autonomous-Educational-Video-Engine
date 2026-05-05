# Autonomous Educational Video Engine (AEVE)

AEVE turns a single prompt — *"Prove the Pythagorean theorem"* — into a
synchronized educational video: Manim animations, edge-TTS narration, and a
single `.mp4` you can play. It runs entirely locally with free / low-cost LLM
providers (Groq, OpenRouter) and no Anthropic dependency.

> **Status (2026-05-03):** AEVE 2.0 is generally available. The 5-agent
> pipeline (Solver → Director → Narrator+TTS → Animator → Render+Healer
> → Assembler) lands a final video end-to-end with Pydantic-gated phase
> boundaries and a 50 ms drift budget. The legacy 10-agent pipeline has
> been retired (Day 7); a CI gate (`tests/test_no_legacy_sync.py`)
> guards against accidental resurrection. See `ORCHESTRATION.md` for
> the agent contract and `CLAUDE.md` for the rewrite history.

---

## Why this exists

A single LLM trying to write a mathematical script + Manim code at the same
time fails reliably:

* **Voice and video drift** — narration ends, animation keeps playing
  (or vice versa), scene to scene the gap compounds.
* **Animations look poor** — everything anchored at the origin, text
  truncated mid-sentence, MathTex falling back to plain Text because LaTeX
  isn't installed.
* **Crashes mid-render** are caught with insufficient context — the LLM
  gets 800 chars of error and guesses.

AEVE 2.0 fixes those structurally: every phase is gated by a Pydantic
schema, every per-scene runtime is gated by an AST predictor against the
ffprobe-measured audio duration, and every render failure ships the full
last-4 KB stderr to a dedicated Healer agent — with a deterministic
fallback scene as the floor so a single bad scene can never block the
pipeline.

---

## Architecture (AEVE 2.0)

```
Phase 0  StyleManifest         deterministic Python (no LLM)
Phase 1  Solver       (S)      math reasoning      → DeepSolution
Phase 2  Director     (D)      scene plan          → Storyboard
Phase 3  Narrator+TTS (N+E)    spoken + word timeline → SceneAudio[]
Phase 4  Animator     (A)      AST-validated Manim → SceneCode[]
Phase 5  Render+Healer (R+H)   render w/ retry + drift correction → SceneVideo[]
Phase 6  Assembler             normalize-then-concat ffmpeg → FinalVideo
```

Phases 3-5 fan out per-scene under `asyncio.Semaphore(4)` for LLM calls and
`asyncio.Semaphore(2)` for render subprocesses. Within a scene the order is
forced (Animator needs the audio's measured duration, Render needs the
animator's code). Every phase boundary is a `Model.model_validate(...)`
call; on validation failure, one repair round is attempted with the error
injected back into the prompt before falling through to the next provider
in the model's fallback chain.

**A/V sync solution.** Drift budget: 50 ms per scene, 50 ms across the
final. Sources of truth in order: ffprobe (authoritative), then AST
predictor (gating), then LLM. We never use `-shortest` (the legacy bug
that truncated audio mid-sentence). See `CLAUDE.md` for the full list.

---

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| Anaconda / Miniconda | Manages the Python env | https://docs.conda.io |
| Python 3.10–3.12 | Pinned in `pyproject.toml` | conda |
| FFmpeg + ffprobe | Concat + duration probe | conda-forge or system |
| Manim CE 0.19.* | The renderer | pip |
| MiKTeX (optional) | LaTeX for `MathTex` — soft requirement | conda or winget |

Run `python setup_check.py` after installation to verify everything's
reachable; it prints per-tool status with install hints for whatever's
missing.

---

## Installation

```powershell
# 1. Create + activate the env
conda create -n cv_conda python=3.10 -y
conda activate cv_conda

# 2. Install the package + deps
pip install -e ".[dev]"

# 3. Verify
python setup_check.py

# 4. Optional: install MiKTeX for high-quality MathTex
python setup_check.py --install-miktex
```

**API keys.** Open `config.py` and paste in your keys (gitignored — keys
never reach the remote):

```python
GROQ_API_KEYS = [
    "gsk_YOUR_FIRST_KEY",
    "gsk_YOUR_SECOND_KEY",   # multiple keys → automatic rotation on 429
]
OPENROUTER_API_KEY = "sk-or-v1-..."
GOOGLE_API_KEY = "AIza..."   # optional fallback
```

---

## Running it

### CLI

```powershell
conda activate cv_conda
python main.py "Prove the Pythagorean theorem" --target-seconds 60
python main.py --image path/to/diagram.png "Explain this image"
python main.py "Derive the quadratic formula" --output-dir ./run01
```

Flags: `--target-seconds` (default 60, clamped to `[20, 180]`),
`--image PATH`, `--output-dir PATH`.

### CLI module entrypoint

```powershell
python -m pipeline.orchestrator "Prove the Pythagorean theorem" --target-seconds 60
```

Same code path as `python main.py "..."`, exposed as a module so it's
easy to import and instrument from tests. Prints a per-scene report:

```
=== AEVE 2.0 phases 0-6 complete ===
topic:       Pythagorean theorem
difficulty:  intermediate
scenes:      5
target:      60s
audio total: 58.42s
final mp4:   D:\...\output\final\final.mp4
final dur:   58.45s (drift +30ms)
  scene 001: audio=10.20s predicted=10.10s rendered=10.21s (drift +10ms)
  scene 002: audio=12.50s predicted=12.50s rendered=12.50s (drift +0ms, healer x1)
  ...
```

### Web UI

```powershell
conda activate cv_conda
python app.py
```

Open http://localhost:5000. The browser-streamed Server-Sent Events
emit a `phase` event per pipeline phase (`phase0` … `phase6`,
`running`/`done`) plus per-line log output, then a final `complete`
event with the playable video URL.

---

## Output layout

```
output/
├── style_manifest.json          # deterministic visual contract (Phase 0)
├── _style.py                    # generated Python constants the Animator imports
├── audio/
│   ├── scene_001.mp3            # narrated audio (edge-tts, ffprobe-measured)
│   ├── scene_001.timeline.json  # word-level offsets from edge-tts WordBoundary
│   └── …
├── scenes/
│   ├── scene_001.py             # generated Manim source (AST-validated)
│   ├── scene_001.carry.json     # per-scene carryover for the next scene
│   └── …
├── video/
│   ├── scene_001.mp4            # per-scene rendered + audio-muxed
│   └── _manim_media/            # raw Manim output (intermediates)
├── final/
│   └── final.mp4                # assembled final video (drift-verified)
└── logs/
    └── pipeline.log             # full debug log
```

---

## Project layout

| Path | Role |
|---|---|
| `pipeline/orchestrator.py` | Top-level `run_pipeline()`; phases 0-6 |
| `pipeline/schemas.py` | Pydantic v2 contracts gating every handoff |
| `pipeline/style.py` | Phase 0 — deterministic StyleManifest builder |
| `pipeline/solver.py` | Phase 1 — `solve(query) → DeepSolution` |
| `pipeline/director.py` | Phase 2 — `direct(solution) → Storyboard` |
| `pipeline/narrator.py` + `pipeline/tts.py` | Phase 3 — narration + edge-tts |
| `pipeline/animator.py` | Phase 4 — Manim code generator + AST gates |
| `pipeline/templates/` | Six fixed layout skeletons |
| `pipeline/timing.py` | ffprobe + AST runtime predictor + pad/trim |
| `pipeline/runtime.py` | `emit_carry()` — runtime helper called from generated scenes |
| `pipeline/carryover.py` | `read_carry` / `write_carry` — cross-scene continuity |
| `pipeline/llm_clients/` | Groq / OpenRouter / Gemini async clients + router |
| `renderer/render.py` | Phase 5a — Manim subprocess + healer-aided retry |
| `renderer/healer.py` | Phase 5b — `heal()` + deterministic fallback |
| `renderer/sanitize.py` | Safe legacy-name renames + Polygon spread |
| `renderer/assembler.py` | Phase 6 — normalize-then-concat (no `-shortest`) |
| `setup_check.py` | Environment verifier (also `aeve-setup-check`) |
| `tests/` | 173 unit tests; live tests gated behind `pytest -m live` |
| `app.py`, `main.py` | AEVE 2.0 entry points (web + CLI) |

See `ORCHESTRATION.md` for the full per-agent contract (model routing,
schemas, repair-round behavior).

---

## Testing

```powershell
# Default: offline only
pytest

# With live LLM/edge-tts probes (consumes API quota)
pytest -m live

# Just one slice
pytest tests/test_animator.py
```

The default suite is `173 passed, 5 deselected` and runs in ~6 s.

---

## Troubleshooting

* **`manim: command not found`** — AEVE 2.0 invokes Manim via
  `python -m manim`, so this only affects the legacy CLI. Run
  `python setup_check.py` to confirm Manim is importable.
* **Groq 429 rate limits** — drop more keys into `GROQ_API_KEYS`. The
  client rotates on every 429.
* **OpenRouter "model not found"** — the OpenRouter client probes
  `/models` at startup and remaps deprecated slugs to the latest variant.
  If the entire family is gone, the registry falls through to the next
  spec in the agent's chain.
* **MathTex looks rough** — LaTeX isn't installed. Run
  `python setup_check.py --install-miktex` to attempt `conda` then
  `winget` install. Failing that, the matplotlib MathTex backend takes
  over (lower quality but doesn't crash).
* **Final video has audible drift** — check
  `output/logs/pipeline.log` for "drift" entries. The 50 ms budget is
  enforced per-scene and across the final; over-budget logs an ERROR but
  doesn't raise. Open an issue with the log + `output/final/<file>.mp4`.

---

## License

Proprietary. See repository root.
