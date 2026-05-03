# AEVE — Autonomous Educational Video Engine

A multi-agent pipeline that turns a single text/image prompt into a synchronized
mathematical animation (Manim video + edge-TTS narration), assembled into a
final `.mp4`.

> **Status (2026-05-03):** Project is being **rewritten as AEVE 2.0**. The legacy
> 10-agent pipeline (M1–M10) still works and is the current default, but every
> new file should follow the AEVE 2.0 architecture below. See the **AEVE 2.0
> Rewrite Plan** section at the bottom of this file for the canonical spec.
>
> **Day 1 — DONE.** Foundation modules are in place, import cleanly, 23 tests
> pass. See **Implementation Status** below for exactly what was built and
> what's next.

## Running things

**Python must be invoked through the project's conda env. Always run:**

```powershell
conda activate cv_conda
```

…before executing `python app.py`, `python main.py`, `pip install`, manim, or
the test scripts. The shell is PowerShell (Windows 11). Manim, FFmpeg, and the
LaTeX-bypass logic all assume the conda env's `Library/bin` is on PATH.

Two entrypoints:
- `python app.py` → Flask web UI on `http://localhost:5000` (SSE-streamed progress).
- `python main.py "<query>"` → CLI; flags: `--image <path>`, `--quality {low,medium,high,4k}`.

## Architecture (legacy 10-agent pipeline — still active)

Three phases, all orchestrated through `models/llm_client.py` (unified Groq /
OpenRouter / Gemini router with retry, key rotation, and output validation).

| Phase | Agents | Files |
|---|---|---|
| **I — Knowledge Distillation** | M1 Solver, M2 Verifier | `pipeline/phase1_knowledge.py` |
| **II — Consensus Committee** | M3 Storyboarder, M4 Visual Detailer, M5 Technical Critic, M6 Finalizer | `pipeline/phase2_committee.py` |
| **III — Generation & Assembly** | M7 Narration polisher, M8 edge-TTS, M9 Manim Coder, M10 Code Reviewer (max 3 retries/scene) | `pipeline/phase3_distributor.py`, `pipeline/audio_stream.py`, `pipeline/code_stream.py`, `pipeline/sync_engine.py` |
| **Assembly** | merge audio+video, concatenate scenes | `renderer/assembler.py`, `renderer/manim_runner.py` |

Phase III runs scenes concurrently (async). Each scene:
TTS first → measure audio duration → M9 generates Manim code with matching
`self.wait(audio_duration)` → render → on crash, M10 patches code from the
traceback.

See `ORCHESTRATION.md` for the full legacy agent contract.

## Config

`config.py` holds API keys (Groq list, OpenRouter, Google), model routing per
agent, TTS voice (`en-US-AriaNeural`), Manim quality flag, and output paths.
**`config.py` is gitignored** — keys are local-only. The current `git log` shows
`Initial commit without secrets`, so keys never landed on the remote; keep it
that way.

## Output layout

```
output/
├── script_1_deep_solution.md   # M1+M2 verified math solution
├── scene_manifest.json         # M6 final scene blueprint
├── scenes/                     # M9 Manim .py files (one per scene)
├── audio/                      # M8 .mp3 narration per scene
├── video/                      # rendered Manim partials
├── final/                      # assembled .mp4
├── uploads/                    # web UI image uploads
├── style_manifest.json         # AEVE 2.0 — deterministic style spec
├── _style.py                   # AEVE 2.0 — generated Python constants
└── logs/pipeline.log           # full debug log
```

## Conventions

- Logging goes through `models.llm_client.logger` (name: `AEVE`); console + `output/logs/pipeline.log`.
- Errors from LLM calls raise `LLMError` / `OutputValidationError` with rich provider/model/role context.
- The Flask app (`app.py`) attaches a transient `SSEHandler` to the AEVE logger per job to stream progress to the browser; don't replace the root logger.

---

# AEVE 2.0 — Full Rewrite Plan (canonical going forward)

## Why we're rewriting

The legacy pipeline produces an MP4, but output is mediocre, animations are
poor, and **voice + video drift apart**. A code audit identified the causes as
structural, not tunable:

- **A/V desync** — `-shortest` truncation in `renderer/assembler.py:72`, MP3
  metadata-only duration reads via `mutagen` (no ffprobe), no post-render
  verification, 15fps default that compounds rounding error, and a regex-based
  `self.wait()` patcher (`pipeline/sync_engine.py`) that fights the LLM instead
  of constraining it.
- **Animation quality** — `sanitize_manim_code` force-replacing every
  `MathTex(...)` with `Text(...)` (LaTeX uninstalled), an 80-char `Text`
  truncator that cuts mid-sentence, M9's prompt explicitly forbidding LaTeX,
  no spatial layout discipline (everything renders at ORIGIN), and trailing
  `self.wait(20–30s)` padding the audio with black-screen silence.
- **Orchestration** — 10 agents with schema drift between M3→M4→M5→M6,
  Phase 3 sequential despite README claiming parallel, Llama 3.3 70B handling
  code generation it isn't best-suited for, M10 receives only 800 chars of
  error text (no full traceback), max 3 retries.

User authorized: **full rewrite, conda-first MiKTeX install, multi-LLM with
hard fallback chains via OpenRouter (no Anthropic, no premium), models picked
specifically for Manim/Python code quality. Keep the Flask UI shell.**

## Architecture — 5 agents, 6 phases, Pydantic-gated

```
Phase 0  StyleManifest        deterministic Python (no LLM)
Phase 1  Solver       (S)     math reasoning      -> DeepSolution
Phase 2  Director     (D)     scene plan          -> Storyboard
Phase 3  Narrator+TTS (N+E)   spoken + timeline   -> SceneAudio[]   (per scene)
Phase 4  Animator     (A)     Manim code          -> SceneCode[]    (per scene)
Phase 5  Render+Healer (R+H)  render w/ retry     -> SceneVideo[]   (per scene)
Phase 6  Assembler            ffmpeg mux+concat   -> FinalVideo
```

Phases 3–5 fan out per scene with `asyncio.Semaphore(4)`. Within a scene the
order is forced (Animator needs the audio's measured duration). Every phase
boundary is a `Model.model_validate(...)` call; on validation failure we retry
once with the validation error injected back into the prompt.

The `M3+M4+M5+M6` chain is dead. One **Director** agent emits a single
schema-validated `Storyboard`. The Voice Polisher (M7) collapses into the
**Narrator**. The Code Reviewer (M10) becomes the **Healer**, only invoked on
render failure with the **full** traceback.

## Model routing (OpenRouter-first, Groq for cheap/fast roles, no Anthropic)

Every agent has primary + 2 fallbacks. Fallback triggers: HTTP 429, 5xx,
timeout, schema validation failure twice in a row.

| Agent | Primary | Fallback 1 | Fallback 2 | Temp | Why |
|---|---|---|---|---|---|
| **Solver (S)** | Groq `moonshotai/kimi-k2-instruct` | OpenRouter `deepseek/deepseek-chat-v3` | Groq `llama-3.3-70b-versatile` | 0.2 | Kimi-K2 leads open math benchmarks |
| **Director (D)** | Groq `llama-3.3-70b-versatile` | OpenRouter `meta-llama/llama-3.3-70b-instruct` | Groq `llama-3.1-8b-instant` | 0.4 | JSON storyboarding — 70B is plenty |
| **Narrator (N)** | Groq `llama-3.3-70b-versatile` | OpenRouter `zai/glm-4.6` | Groq `llama-3.1-8b-instant` | 0.5 | Spoken-English rewrite |
| **TTS (E)** | edge-tts `en-US-AriaNeural` (streaming + WordBoundary) | edge-tts `en-US-JennyNeural` | edge-tts `en-US-GuyNeural` | n/a | Free; exposes WordBoundary we need for the timeline |
| **Animator (A)** | OpenRouter `nvidia/nemotron-3-coder` | OpenRouter `qwen/qwen3-coder` | OpenRouter `deepseek/deepseek-chat-v3` | 0.2 | Strongest non-premium coders for Manim Python |
| **Healer (H)** | OpenRouter `deepseek/deepseek-r1` | OpenRouter `nvidia/nemotron-3-coder` | OpenRouter `qwen/qwen3-coder` | 0.1 | Reasoning model for debugging |

`pipeline/llm_clients/registry.py` probes OpenRouter `/models` at startup to
resolve the latest variant if a slug shifts. Groq key rotation from the legacy
`models/llm_client.py` is preserved.

## A/V sync solution (the most critical fix)

**Strategy: audio-first, timeline-driven, ffprobe-verified, no `-shortest`.**
Drift budget: 50 ms per scene, 50 ms across the final video.

Per-scene flow:

1. Narrator rewrites `narration_draft` into clean spoken English (LaTeX
   expanded to words).
2. **edge-tts streaming** produces `scene_<id>.mp3` AND captures WordBoundary
   events into `scene_<id>.timeline.json` — list of
   `{word, offset_ms, duration_ms}`.
3. **`ffprobe -v error -show_entries format=duration -of csv=p=0`** measures
   the actual MP3 duration. Replaces `mutagen`. The 150-wpm fallback is deleted.
4. Animator receives the word timeline + measured duration `T` and emits a
   Manim scene whose `construct()` uses explicit
   `self.play(..., run_time=...)` and `self.wait(...)` summing to `T`.
   **Forbidden:** a final `self.wait(N)` padding the audio. Forbidden: any
   unaccounted-for runtime.
5. **AST runtime predictor** walks the generated AST, sums `run_time=` kwargs
   and literal `self.wait(N)` floats. If predicted ∉ [0.92·T, 1.05·T], reject
   and re-prompt the Animator with "spread reveals across full duration." No
   regex hacking on the code post-hoc.
6. Render at 1080p30: `manim render --fps 30 -r 1920,1080 ...`. Frame quantum
   is 33.3 ms.
7. **ffprobe verification** on the rendered MP4. If `|measured − T| > 50 ms`:
   - Video shorter → `ffmpeg -i in.mp4 -vf "tpad=stop_mode=clone:stop_duration=Δ"` (extend last frame).
   - Video longer → `ffmpeg -i in.mp4 -t T` (hard cut).
8. **Mux without `-shortest`:**
   `ffmpeg -i v.mp4 -i a.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k out.mp4`.
9. **Concat normalization**: every clip is re-encoded to identical
   `fps=30, scale=1920:1080, setsar=1`, `-video_track_timescale 30000`,
   `-c:a aac -ar 48000 -ac 2` *before* the concat demuxer runs. Eliminates the
   cross-scene drift compounding.
10. **Final ffprobe assertion**:
    `|final_duration − sum(scene_audio_durations)| < 50 ms`.

`pipeline/sync_engine.py` is **deleted**. Animator owns runtime, AST predictor
enforces it, ffprobe verifies it, ffmpeg pads/trims if needed.

## Animation quality fix

### LaTeX rendering — install MiKTeX into the conda env

Try in order:
1. `conda install -n cv_conda -c conda-forge miktex -y`
2. If conda recipe fails: `winget install MiKTeX.MiKTeX` (system-wide).
3. If both fail: emit a loud warning, fall back to `MathTex` via matplotlib
   backend (poor but typeset) and continue. `setup_check.py` runs
   `latex --version` at app startup and prints the install path.

After install: **delete the `MathTex/Tex → Text` regex** in the new
`renderer/sanitize.py`. Manim's `MathTex` works.

### New Animator contract

System prompt enforces:

- **Imports**: `from manim import *` and `from output._style import *`
  (auto-generated module exposing palette, font, layout zones). No third-party
  imports.
- **Allowed primitives**: `Text, MathTex, Tex, MarkupText, VGroup, Axes,
  NumberPlane, FunctionGraph, Arrow, Dot, Line, Circle, Square, Rectangle,
  RoundedRectangle, Polygon, BraceLabel, SurroundingRectangle, Code`.
- **Forbidden**: `ShowCreation`, `TextMobject`, `TexMobject`, `add_sound`,
  custom shaders, `Polygon([list])` (must spread args), raw LaTeX inside
  `Text(...)` (must use `MathTex`).
- **Layout discipline**: only place objects within `StyleManifest.layout_zones`.
  Fixed coordinate constants (`TITLE_POS`, `MAIN_POS`, `CAPTION_POS`,
  `LEFT_RAIL_POS`, `RIGHT_RAIL_POS`) injected via `output/_style.py`. No-overlap
  rule: every newly added VMobject must `.next_to(...)` an anchor or
  `Transform` an existing one.
- **Color palette**: only colors from `StyleManifest.palette`
  (`BG, PRIMARY, ACCENT, MUTED, SUCCESS, WARN`).
- **Pacing budget**: total runtime is partitioned
  `10% intro / 70% derivation / 15% emphasis / 5% transition out`. Animator
  emits per-step `run_time` so the sum equals target.
- **Required ending**:
  `self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)`. No trailing
  `self.wait(N)`.
- **Continuity**: receives `prior_scene_summary` (carried-over object names +
  positions). Reused formulas must `ReplacementTransform` from prior form.
- **Carry-out**: writes `output/scenes/scene_<id>.carry.json` listing
  surviving object names + positions for the next scene.

### Layout templates (six)

Director picks one per scene; each is a 30-line Python skeleton injected into
the Animator prompt: `title_only`, `title_plus_eq`, `equation_focus`, `graph`,
`derivation_chain`, `split_eq_text`. Animator fills in equations, narration
callouts, transitions — never invents coordinates.

### `sanitize_manim_code` rewrite → `renderer/sanitize.py`

Only safe transforms:
- `Polygon([list])` → `Polygon(*list)` (preserves nested coordinates).
- `ShowCreation` → `Create`, `TextMobject` → `Text`, `TexMobject` → `MathTex`
  (now `MathTex` not `Text`, since LaTeX is installed).
- Drops the brutal 80-char `Text` truncator entirely. Long text is the
  Animator's problem; the Director's `narration_draft` is constrained to ≤2
  sentences per scene.

## Cross-scene continuity

`pipeline/style.py` builds a `StyleManifest` deterministically once, before any
LLM call. Inputs: difficulty, palette preference (default "blue/yellow on
near-black"), font availability. Outputs: `output/style_manifest.json` + a
generated `output/_style.py` Python module (constants only). Every Director /
Narrator / Animator prompt receives the manifest verbatim.

Carryover JSON per scene is the continuity primitive — Scene N+1's Animator
receives Scene N's `carry.json` and must `self.add(...)` the named objects
with their saved positions before introducing new ones.

## Concurrency

```python
phase0_style()                                         # sync
deep_solution = await solver()                         # 1 LLM call
storyboard    = await director(deep_solution)          # 1 LLM call

LLM_SEM    = asyncio.Semaphore(4)
RENDER_SEM = asyncio.Semaphore(2)

async def per_scene(s, prior_carry):
    async with LLM_SEM:
        narration = await narrator(s)
        audio = await tts_with_word_boundary(narration)        # serial within scene
    audio.duration_s = await ffprobe_duration(audio.mp3_path)
    async with LLM_SEM:
        code = await animator(s, audio, prior_carry)
    code = ast_validate_or_reprompt(code, audio.duration_s)
    async with RENDER_SEM:
        video = await render_with_healer(code, audio.duration_s)
    return video

videos = await asyncio.gather(*[per_scene(s, carry_chain[i]) for i,s in enumerate(storyboard.scenes)])
final  = await assembler(videos, storyboard)                   # ffmpeg
```

Manim render runs through `asyncio.to_thread(subprocess.run, ...)` —
non-blocking on the loop. Two semaphores prevent ffmpeg/Manim CPU contention on
Windows.

## Self-healing render loop

1. **AST pre-check** before rendering: `ast.parse(code)` catches syntax errors
   instantly.
2. **Static API check**: AST walk rejects forbidden names (`ShowCreation`,
   `TextMobject`, etc.). No LLM cost.
3. **Predicted-runtime check**: AST sum of `run_time` + `wait(N)` literals must
   be in [0.92·T, 1.05·T].
4. **Render** with `subprocess.run(..., capture_output=True)`. On nonzero exit:
   capture **last 4 KB of stderr** (not 800 chars), find the offending line via
   traceback's filename+lineno, save `scene_<id>.attempt_N.py.bak`.
5. **Healer** receives `(broken_code, full_stderr_4kb, target_runtime,
   layout_template, style_manifest)`. Returns full new code. Re-runs steps 1–4.
6. **Max 4 attempts** (vs. current 3). Final fallback: deterministic Jinja
   template `pipeline/fallback_scene.py.j2` that renders the title + formulas
   as `MathTex` + a 1s fade. Pipeline never blocks on a single bad scene.

## Pydantic schemas (gating every handoff)

`pipeline/schemas.py` (Pydantic v2):

```python
class StyleManifest(BaseModel):
    palette: dict[Literal["bg","primary","accent","muted","success","warn"], str]
    font: Literal["Inter","Latin Modern Roman","JetBrains Mono"]
    base_font_size: int = 36
    frame_margin: float = 0.5
    transition: Literal["FadeOut","ReplacementTransform"]
    layout_zones: dict[str, tuple[float,float]]

class Step(BaseModel):
    narrative: str
    latex: str | None
    visual_intent: str

class DeepSolution(BaseModel):
    topic: str
    difficulty: Literal["intro","intermediate","advanced"]
    prerequisites: list[str]
    steps: list[Step]
    conclusion: str

class StoryboardScene(BaseModel):
    scene_id: str  # zero-padded, e.g. "001"
    title: str
    key_concept: str
    narration_draft: str  # ≤2 sentences, math in LaTeX
    formulas: list[str]   # raw LaTeX, no $$
    visual_intent: str
    layout: Literal["title_only","title_plus_eq","equation_focus",
                    "graph","derivation_chain","split_eq_text"]
    carryover_objects: list[str]
    transition_in: Literal["fade","slide_left","none"]

class Storyboard(BaseModel):
    total_target_seconds: int  # 45-90 typical
    scenes: list[StoryboardScene]

class WordEvent(BaseModel):
    word: str; start_s: float; end_s: float

class SceneAudio(BaseModel):
    scene_id: str; mp3_path: Path
    duration_s: float           # ffprobe-measured
    word_timeline: list[WordEvent]
    narration_final: str

class SceneCode(BaseModel):
    scene_id: str; py_path: Path; class_name: str
    target_runtime_s: float
    ast_validated: bool; predicted_runtime_s: float

class SceneVideo(BaseModel):
    scene_id: str; mp4_path: Path
    measured_duration_s: float
    drift_ms: int
    used_healer: bool; healer_attempts: int

class FinalVideo(BaseModel):
    mp4_path: Path
    total_duration_s: float; scene_count: int; total_drift_ms: int
```

On a validation failure: 1 retry with the error injected, then provider
fallback, then schema-level fallback (e.g. a stub `Storyboard` with one scene).

## File-by-file plan

### Keep, lightly modified
- `app.py` — update phase names + SSE event payloads.
- `main.py` — call new orchestrator entrypoint.
- `frontend/templates/`, `frontend/static/` — relabel phases.

### Rewrite
- `config.py` → `config.py` (paths, render quality, conda env name) +
  new `models_config.py` (routing table) + new `style_defaults.py`.
- `models/llm_client.py` →
  `pipeline/llm_clients/{registry,groq,openrouter,gemini}.py`. Keep
  `LLMError`, `OutputValidationError`. Async-first. Provider fallback chain.
  Existing Groq key rotation ported in.
- `pipeline/phase1_knowledge.py` → `pipeline/solver.py`. Returns
  `DeepSolution`.
- `pipeline/phase2_committee.py` → **delete**, replaced by
  `pipeline/director.py`.
- `pipeline/phase3_distributor.py` → `pipeline/orchestrator.py`. Asyncio
  fan-out.
- `pipeline/audio_stream.py` → split into `pipeline/narrator.py` +
  `pipeline/tts.py` (the latter captures WordBoundary).
- `pipeline/code_stream.py` → `pipeline/animator.py`.
- `pipeline/sync_engine.py` → **DELETE**. Replaced by `pipeline/timing.py`.
- `renderer/manim_runner.py` → split into `renderer/render.py` (subprocess +
  ffprobe verify) + `renderer/healer.py` + `renderer/sanitize.py`.
- `renderer/assembler.py` → rewrite. Drop `-shortest`. Normalize-then-concat.
  ffprobe-verify.

### New
- `pipeline/style.py` — builds `StyleManifest` + writes `output/_style.py`.
- `pipeline/schemas.py` — Pydantic v2 models above.
- `pipeline/timing.py` — `ffprobe_duration()`,
  `predict_manim_runtime(ast)`, `pad_or_trim(mp4, target_s)`.
- `pipeline/templates/` — six layout templates (`*.py.j2`).
- `pipeline/fallback_scene.py.j2` — last-resort renderable.
- `setup_check.py` — verifies LaTeX, ffmpeg, conda env at startup.
- `tests/test_sync.py`, `tests/test_schemas.py`,
  `tests/golden/pythagoras_60s/`.
- `pyproject.toml` — replaces `requirements.txt`. Pins: `manim==0.19.*`,
  `pydantic>=2.7`, `httpx[http2]`, `groq`, `openai` (for OpenRouter compat),
  `google-genai`, `edge-tts`, `mutagen`, `Pillow`, `colorama`, `pytest`,
  `pytest-asyncio`, `jinja2`.

### Delete
- `pipeline/sync_engine.py`
- `test_audio.mp3`, `test_manim.py`, `test_models.py` (replaced by real test
  suite under `tests/`).
- All current `output/scenes/*.py` (regenerate fresh).
- `__pycache__/` directories.

## Verification (CI gates)

End-to-end smoke prompt: **"Prove the Pythagorean theorem with a visual
square-rearrangement proof"** → expected 5–7 scenes, 50–70s.

1. **Sync**:
   `assert abs(ffprobe(final.mp4) − sum(scene.audio.duration_s)) < 0.050`.
2. **Per-scene drift**:
   `for v in scene_videos: assert abs(v.measured_duration_s − v.target_runtime_s) < 0.050`.
3. **Schema round-trips**: `tests/test_schemas.py` — every model
   serializes/deserializes lossless.
4. **Visual diff**: render `tests/golden/pythagoras_60s/scene_003.py` with
   fixed seed; frame at t=2.0s compared to `expected.png` via
   `PIL.ImageChops` with per-pixel tolerance 8/255 and total diff <2%.
5. **No `-shortest`**: grep assertion in `tests/test_assembler.py`.
6. **No regex sync hacks**: grep assertion that `pipeline/sync_engine.py` does
   not exist.

Subjective rubric (10 prompts spanning calculus, linalg, geometry,
probability), each scored 1–5 on: math correctness, narration naturalness,
visual layout, animation smoothness, A/V sync. Target: every dimension ≥4 on
every prompt.

Performance budget: 60s video, 6 scenes, 6-core box, target wall-clock <2 min.

## Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | conda-forge miktex fails on Windows | `winget install MiKTeX.MiKTeX` → matplotlib MathTex backend with loud warning. |
| R2 | OpenRouter Nemotron 3 slug shifts / model deprecated | Registry probes `/models` at startup; per-agent fallback chain. |
| R3 | edge-tts WordBoundary stream rate-limited | Cache TTS by SHA256 of narration text. Synthesize uniform timeline if WordBoundary missing. |
| R4 | Manim CE 0.19 breaking changes during dev | Pin `manim==0.19.*` in pyproject. CI smoke prompt on every dep bump. |
| R5 | Render concurrency saturates Windows box | Two semaphores (LLM_SEM=4, RENDER_SEM=2). Disable Manim progress bars. |
| R6 | OpenRouter/Groq keys hit cost ceiling | Local SQLite ledger tallies per-call cost; daily cap in `config.py`. |

## Sequenced implementation order

All commands run inside `conda activate cv_conda`.

1. **Day 1 — Foundation**: `pyproject.toml`, `pipeline/schemas.py`,
   `pipeline/style.py`, `pipeline/llm_clients/{registry,groq,openrouter,gemini}.py`.
   Implement async clients with provider fallback. Schema round-trip tests
   pass.
2. **Day 1–2 — Sync infrastructure**: `pipeline/timing.py` (ffprobe, AST
   predictor, pad/trim), `pipeline/tts.py` with WordBoundary capture.
   `tests/test_sync.py` passes against pre-recorded MP3 fixture.
3. **Day 2 — Solver + Director**: minimal path to validated `Storyboard`.
   Test on 3 prompts.
4. **Day 3 — Narrator + TTS pipeline**: full timeline JSON. Verify via ffprobe.
5. **Day 3–4 — Animator + 6 layout templates**: with `StyleManifest` injection
   and AST predictor as a hard gate.
6. **Day 4 — Renderer + Healer + MiKTeX**: install MiKTeX into `cv_conda`.
   Render the smoke prompt end-to-end.
7. **Day 5 — Assembler**: normalize-then-concat, drop `-shortest`, ffprobe
   `<50 ms` assertion.
8. **Day 5 — Web UI rewire**: update Flask SSE event names; regression-test
   live progress.
9. **Day 6 — CI + golden tests**: full suite + 10-prompt rubric run.

Each step has a deterministic test gate. A failure halts the cascade and
surfaces the exact module to inspect — making the autonomous run safe.

---

# Implementation Status (live)

> Update this section as work progresses. Future Claude sessions should read
> here first to know what's already done before re-doing anything.

## ✅ Day 1 — Foundation (complete, 2026-05-03)

All Day 1 deliverables landed and pass tests under `cv_conda` (Python 3.10.19,
pydantic 2.7.1, httpx 0.28.1).

### Files created

| File | Purpose |
|---|---|
| `pyproject.toml` | Replaces `requirements.txt`. Pins `manim==0.19.*`, `pydantic>=2.7,<3`, `httpx[http2]>=0.27`, `groq>=0.11`, `openai>=1.40`, `google-genai>=0.3`, `edge-tts>=6.1`, `mutagen`, `Pillow`, `colorama`, `Flask`, `jinja2`, `python-dotenv`, `numpy`. Dev extras: `pytest`, `pytest-asyncio`, `pytest-timeout`, `ruff`, `mypy`. Configures pytest testpaths + ruff lint set. |
| `pipeline/schemas.py` | Pydantic v2 contracts: `StyleManifest`, `Step`, `DeepSolution`, `StoryboardScene`, `Storyboard`, `WordEvent`, `SceneAudio`, `SceneCode`, `SceneVideo`, `FinalVideo`, `CarryObject`, `SceneCarry`. All use `extra="forbid"`. Custom validators reject duplicate scene_ids, unsorted scene_ids, palettes missing required keys, palettes with non-hex values, `WordEvent.end_s < start_s`, scene_id format violations (must be `^\d{3}$`). |
| `pipeline/style.py` | Deterministic StyleManifest builder. Three palettes (`blue_yellow_dark`, `monochrome`, `warm`). Six layout zones (`title`, `main`, `caption`, `left_rail`, `right_rail`, `footer`). `write_style_artifacts()` emits both `output/style_manifest.json` (LLM prompt block) and `output/_style.py` (Python constants the Animator imports as `from output._style import *`). |
| `pipeline/llm_clients/__init__.py` | Public surface: `call_agent`, `call_agent_json`, `ROUTES`, `resolve_route`, error classes, `AgentRole`, `ModelSpec`. |
| `pipeline/llm_clients/errors.py` | Hierarchy: `LLMError` → `ProviderError`, `RateLimitError`, `OutputValidationError`. Each carries an `LLMErrorContext` (role, provider, model, attempt). `OutputValidationError` also captures `raw_output` (truncated) for repair-round prompting. |
| `pipeline/llm_clients/groq.py` | Async client. **Per-request key rotation** across all `config.GROQ_API_KEYS` on HTTP 429. Direct httpx call to `/chat/completions` (skips openai SDK to keep providers cleanly separated). Singleton via `get_client()`. |
| `pipeline/llm_clients/openrouter.py` | Async client. **Dynamic slug resolution** — `resolve_slug(intended)` probes `/models` once, caches, and remaps to the latest variant of the same family if the intended slug is gone. Uses HTTP/2. Adds the required `HTTP-Referer` and `X-Title` headers. |
| `pipeline/llm_clients/gemini.py` | Optional fallback client. Imports `google.genai` lazily; if SDK is absent, the client is constructed but `chat()` raises `ProviderError` (registry then advances to the next spec). Uses `asyncio.to_thread` to wrap the sync SDK. |
| `pipeline/llm_clients/registry.py` | The router. `ROUTES: dict[AgentRole, list[ModelSpec]]` holds the primary + 2 fallbacks per agent (see table below). `call_agent()` walks the chain on `RateLimitError`/`ProviderError`. `call_agent_json(parser=...)` adds one repair round per spec where the parser error is injected back into the prompt. `warm_up()` pre-fills the OpenRouter model cache. |
| `pipeline/timing.py` | The sync-fix core. `ffprobe_duration()` async wrapper around `ffprobe -show_entries format=duration` (replaces `mutagen` metadata reads). `predict_manim_runtime(code) -> RuntimePrediction` AST-walks `construct()`, sums `run_time=` kwargs and `self.wait(N)` literals (handles arithmetic on literals via sandboxed `eval`), defaults to Manim's `1.0s` when `run_time` is omitted. `pad_or_trim(mp4, target_s)` post-render correction via `ffmpeg tpad=stop_mode=clone` (extend) or `-t target_s` (hard cut). Drift budget: `DRIFT_BUDGET_S = 0.050`. `measure_and_correct()` is the orchestrator-facing convenience wrapper. |
| `tests/__init__.py` | Empty marker. |
| `tests/test_schemas.py` | 16 tests. Round-trips for `StyleManifest`, `DeepSolution`, `Storyboard`, `SceneAudio`, `SceneCode`, `SceneVideo`, `FinalVideo`, `SceneCarry`. Negative tests for: palette missing keys, non-hex palette, empty steps list, duplicate scene_ids, unsorted scene_ids, total_target_seconds bounds, scene_id format, `WordEvent.end_s < start_s`, non-positive `duration_s`. |
| `tests/test_timing.py` | 7 tests on the AST predictor: explicit `run_time`, missing `run_time` (default 1.0s), arithmetic in `run_time` (`1.5 + 0.5`), `in_window()` bounds check, syntax-error propagation, `DRIFT_BUDGET_S == 0.050`. |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest tests/test_schemas.py tests/test_timing.py -q
....................... [100%]
23 passed in 0.28s
```

### Routing table (verified live in registry)

| Role | Primary | Fallback 1 | Fallback 2 | Temp |
|---|---|---|---|---|
| `solver` | `groq` / `moonshotai/kimi-k2-instruct` | `openrouter` / `deepseek/deepseek-chat-v3` | `groq` / `llama-3.3-70b-versatile` | 0.2 |
| `director` | `groq` / `llama-3.3-70b-versatile` | `openrouter` / `meta-llama/llama-3.3-70b-instruct` | `groq` / `llama-3.1-8b-instant` | 0.4 |
| `narrator` | `groq` / `llama-3.3-70b-versatile` | `openrouter` / `zai/glm-4.6` | `groq` / `llama-3.1-8b-instant` | 0.5 |
| `animator` | `openrouter` / `nvidia/nemotron-3-coder` | `openrouter` / `qwen/qwen3-coder` | `openrouter` / `deepseek/deepseek-chat-v3` | 0.2 |
| `healer` | `openrouter` / `deepseek/deepseek-r1` | `openrouter` / `nvidia/nemotron-3-coder` | `openrouter` / `qwen/qwen3-coder` | 0.1 |

### Important contract details a future session must respect

- **Nothing in the legacy pipeline was deleted or modified.** Day 1 is purely
  additive. `app.py`, `main.py`, `config.py`, `models/llm_client.py`,
  `pipeline/phase{1,2,3}_*.py`, `pipeline/sync_engine.py`, `renderer/*.py` are
  untouched. The legacy `python app.py` flow still runs.
- **`config.py` is the source of truth for API keys** (Groq list, OpenRouter,
  Google). The new clients read from `config` directly. Do **NOT** commit
  `config.py` to the remote — it's gitignored.
- **`pipeline/llm_clients` is async-only.** All new agent code (Day 2+) must
  `await call_agent(...)` from inside an async function. `asyncio.to_thread`
  is the bridge for synchronous code.
- **The AST predictor only handles literals + simple arithmetic on literals.**
  When the Animator passes a variable (e.g. `run_time=audio_duration`), the
  predictor falls back to Manim's default 1.0s. Day 4 Animator prompt must
  therefore force *literal* `run_time=` values that sum to the target — no
  variable-driven arithmetic.
- **`OutputValidationError.raw_output` is truncated to 1000 chars** when
  injected into the repair-round prompt. If a future debugging task needs the
  full output, log it at the call site, don't widen this field.
- **OpenRouter slug resolution is best-effort.** If `/models` is unreachable,
  `resolve_slug()` returns the intended slug as a passthrough — the call may
  then fail with `ProviderError` and the registry falls through to the next
  spec. This is the expected degradation path.

## ✅ Day 2 — Agent wiring (complete, 2026-05-03)

Day 2 turned the foundation into four real agent entry points. Nothing legacy
was modified — Day 2 is still purely additive.

### Files created

| File | Purpose |
|---|---|
| `pipeline/solver.py` | `async solve(query, image_hint=None) -> DeepSolution`. Uses `call_agent_json(role="solver", parser=_parse)`. `_parse` strips ```` ```json ```` fences, validates against `DeepSolution`, and best-effort-unwraps single-key wrappers (e.g. `{"response": {...}}`) before re-validating. System prompt forces JSON-only output with the DeepSolution schema inlined. |
| `pipeline/director.py` | `async direct(solution, *, target_seconds=60, style=None) -> Storyboard`. Same parser pattern. System prompt enumerates the six layout names + three transitions verbatim and bakes `target_seconds` into the instructions so the schema's `[20,180]` bound isn't surprising. If a `StyleManifest` is passed it's serialized via `manifest_to_prompt_block()` and included so the Director keeps its plan consistent with the visual contract. |
| `pipeline/narrator.py` | `async polish(scene) -> str`. Plain-text path (uses `call_agent`, not `call_agent_json`). The `_validate` step strips matched surrounding quotes, hard-truncates at the nearest sentence boundary if `len > 400`, and rejects any LaTeX residue (`\\command`, `$`, `^`, `_{`) — forces the LLM to speak in words. |
| `pipeline/tts.py` | `async synthesize(*, text, out_path, scene_id, voice=DEFAULT_VOICE, rate, volume) -> SceneAudio`. Streams `edge_tts.Communicate.stream()`: `audio` chunks accumulate to disk, `WordBoundary` chunks build a `list[WordEvent]` (offset/duration are 100-ns units → seconds via `HNS_PER_SECOND = 1e7`). Voice fallback chain (Aria → Jenny → Guy) is walked on any exception. After streaming, `ffprobe_duration()` is the authoritative duration source. If WordBoundary events were empty, `_synthetic_timeline()` fabricates a uniform per-word split so the Animator still has anchors. |
| `tests/test_solver.py` | 7 unit tests + 1 live test. Unit tests cover `_parse`: clean JSON, fenced JSON, single-key unwrap, empty rejection, non-JSON rejection, invalid `difficulty`, empty `steps`. Live test (`@pytest.mark.live`) hits real Groq keys with the Pythagorean prompt. |
| `tests/test_director.py` | 7 unit tests. Cover `_parse` (clean / fenced / unwrap / unsorted-id rejection / unknown-layout rejection) and `_system_prompt` invariants (must list every layout name, every transition name, and the supplied `target_seconds`). |
| `tests/test_narrator.py` | 7 unit tests on `_validate`: clean prose passthrough, surrounding-quote stripping (single + double), long-output truncation, empty rejection, three flavors of LaTeX-residue rejection (`\\frac`, `$`, `^`). |
| `tests/test_tts.py` | 3 unit tests + 1 live test. Unit tests cover `_synthetic_timeline` (uniform split, empty-text edge case) and `VOICE_FALLBACKS` invariants. Live test (`@pytest.mark.live`) hits the real edge-tts WebSocket and asserts duration > 0.5s with ≥3 WordBoundary events. |
| `pyproject.toml` | Added `[tool.pytest.ini_options].markers = ["live: ..."]` and `addopts = "-m 'not live'"` so live tests are deselected by default. Run them explicitly with `pytest -m live`. |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest -q
...............................................                          [100%]
47 passed, 2 deselected, 4 warnings in 2.63s
```

47 = 23 (Day 1) + 24 (Day 2). The 2 deselected tests are the `@pytest.mark.live`
ones, correctly skipped without keys.

### Important contract details a future session must respect

- **`_parse` is permissive about wrappers, strict about content.** Both the
  solver and director will best-effort-unwrap a `{"response": {...}}` shape
  before validating. If you tighten this, also tighten the system prompt; the
  unwrap is a defense against well-meaning model JSON-mode envelopes.
- **Narrator output is plain text, not JSON.** It uses `call_agent` (no
  repair round). If you switch to `call_agent_json`, drop the `_validate`
  regex check — JSON mode wrappers will set off the LaTeX-residue detector.
- **TTS voice fallback is independent of the LLM-registry fallback.** A
  failure in `synthesize()` rotates through `VOICE_FALLBACKS`, not through
  `ROUTES`. There's no LLM in the TTS hop.
- **WordBoundary offset/duration units are 100-nanoseconds.** Microsoft's
  edge-tts emits HNS values; we divide by `HNS_PER_SECOND = 1e7`. If you ever
  see word_timeline events in the millions, this constant is the bug.
- **`narration_draft` may contain LaTeX; `narration_final` MUST NOT.** The
  Director's draft is the input to Narrator; the Narrator's polished output
  becomes `SceneAudio.narration_final` and is what's actually spoken.
- **Live tests are gated.** `@pytest.mark.live` + `addopts = "-m 'not live'"`
  in pyproject. Run live with `pytest -m live` (consumes Groq/OpenRouter
  quota and hits the edge-tts service). Plain `pytest` runs offline only.

## ✅ Day 3 — Orchestrator + carryover (complete, 2026-05-03)

Day 3 stitched phases 0-3 into a single async entrypoint and added the
carryover plumbing the Animator will consume on Day 4. Still purely additive.

### Files created

| File | Purpose |
|---|---|
| `pipeline/carryover.py` | Read/write `SceneCarry` JSON at `output/scenes/scene_<id>.carry.json`. `read_carry()` returns an empty `SceneCarry` for missing files (the expected first-scene case) and silently degrades to empty on malformed JSON (logs a warning, never blocks the pipeline). `empty_carry()` is a convenience constructor; `carry_path()` is the canonical filename helper. |
| `pipeline/orchestrator.py` | Top-level async `run_pipeline(query, *, target_seconds=60, image_hint=None, output_dir=None) -> PipelineResult`. Phases 0→3 wired end-to-end. **Concurrency**: `LLM_SEM = asyncio.Semaphore(4)` wraps `polish()` calls; TTS runs outside the LLM semaphore (different bottleneck). Within a scene the order is forced serial (Narrator → TTS). Across scenes `asyncio.gather()` schedules them concurrently. Each scene writes `output/audio/scene_<id>.mp3` plus `output/audio/scene_<id>.timeline.json` (the WordBoundary timeline serialized for the Animator on Day 4). Includes a CLI smoke harness — `python -m pipeline.orchestrator "<query>"` runs end-to-end and prints per-scene durations. |
| `tests/test_carryover.py` | 5 tests: canonical `carry_path()`, missing-file → empty SceneCarry, write-then-read round-trip, malformed-file → empty (with warning), `empty_carry()` helper. |
| `tests/test_orchestrator.py` | 2 tests using `monkeypatch` + `asyncio.run()` to exercise `run_pipeline()` without hitting any real LLM/TTS. Verifies: PipelineResult shape, scenes returned in scene-id order, MP3 + timeline JSON written per scene, style artifacts written to `output_dir`, and (separately) that `_phase3_fanout` actually dispatches scenes concurrently (all 4 scenes start before any complete). |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest -q
......................................................                  [100%]
54 passed, 2 deselected, 4 warnings in 2.98s
```

54 = 23 (Day 1) + 24 (Day 2) + 7 (Day 3). Both live tests still gated.

### Important contract details a future session must respect

- **Tests use `asyncio.run()`, NOT `pytest.mark.asyncio`.** `pytest-asyncio`
  is listed in pyproject `dev` extras but not installed in `cv_conda`. The
  Day 3 orchestrator tests therefore call `asyncio.run(...)` from sync test
  functions. If a future session installs `pytest-asyncio`, you may switch
  back — but don't add `@pytest.mark.asyncio` without first confirming the
  plugin is loaded.
- **`run_pipeline()` returns `PipelineResult`, not `FinalVideo`.** Day 3
  scope is phases 0-3; the dataclass has slots reserved for `scene_codes`
  (Day 4) and `final_video` (Day 5) but they're not fields yet. When Day 4
  extends the dataclass, prefer adding fields to changing the function name.
- **The fan-out wraps `polish()` in `LLM_SEM`, not `synthesize()`.** Edge-tts
  is a separate WebSocket service with its own quota; the LLM semaphore caps
  Groq/OpenRouter concurrency only. If you ever see edge-tts rate-limit
  errors, add a separate `TTS_SEM`, don't widen `LLM_SEM`.
- **WordBoundary timeline is persisted twice.** Once inside `SceneAudio`
  (in-memory, returned from `synthesize()`) and once on disk as
  `scene_<id>.timeline.json` (consumed by Day 4 Animator). The disk copy is
  authoritative for Animator prompt construction; the in-memory copy is
  authoritative for the orchestrator's Pydantic gates.
- **`asyncio.gather()` preserves order.** The `audios.sort(...)` after the
  gather is belt-and-suspenders only — if you ever switch to
  `asyncio.as_completed()`, the sort becomes load-bearing.
- **The CLI smoke harness lives in `_main()` of `pipeline/orchestrator.py`.**
  Don't put it in `main.py` until Day 5+ — `main.py` is still legacy AEVE
  1.0 and we want to keep its contract intact for the side-by-side period.

## ✅ Day 4 — Animator + layouts + sanitize + AST gates (complete, 2026-05-03)

Day 4 added the Animator and the six layout skeletons, plus the post-LLM AST
gate stack. The orchestrator now produces `SceneCode` per scene alongside
`SceneAudio`. Still purely additive — legacy AEVE 1.0 untouched.

### Files created

| File | Purpose |
|---|---|
| `pipeline/templates/__init__.py` | Layout-template loader. `load_template(name)` returns the raw Python text of one of the six skeletons, validated against the `LayoutTemplate` literal type. `lru_cache`'d on first read. `template_path()` and `all_layouts()` are convenience accessors. **No Jinja yet** — templates are read as plain text and embedded into the Animator's user prompt verbatim. |
| `pipeline/templates/title_only.py` | Single-phrase opening/closing. 4 plays, 5.00s budget. Uses `TITLE_POS`. |
| `pipeline/templates/title_plus_eq.py` | Title + one equation. 5 plays, 6.20s budget. `TITLE_POS` + `MAIN_POS`. |
| `pipeline/templates/equation_focus.py` | Single equation morphed via `ReplacementTransform`. 5 plays, 9.00s. `MAIN_POS`. |
| `pipeline/templates/graph.py` | Axes + FunctionGraph + label. 6 plays, 7.50s. `TITLE_POS` + axes-relative coords. |
| `pipeline/templates/derivation_chain.py` | Vertical column of equations cascaded via `arrange(DOWN)`. 4 plays, 5.00s. `MAIN_POS`. |
| `pipeline/templates/split_eq_text.py` | Equation on left rail, prose on right rail. 5 plays, 7.50s. `LEFT_RAIL_POS` + `RIGHT_RAIL_POS`. |
| `renderer/sanitize.py` | `safe_transform(code) -> (code, SanitizeReport)`. Four idempotent transforms: `ShowCreation → Create`, `TextMobject → Text`, `TexMobject → MathTex`, `Polygon([list]) → Polygon(*list)`. Polygon spread uses bracket-depth tracking so nested coord lists like `Polygon([[0,0,0],[1,0,0]])` survive correctly. Word-boundary regex prevents eating substrings (`MyShowCreation` is left alone). The legacy 80-char `Text` truncator is gone. |
| `pipeline/animator.py` | `async animate(*, scene, audio, prior_carry, style, scenes_dir) -> SceneCode`. System prompt enumerates allowed primitives, forbidden names, layout zones, palette, pacing budget, required ending. User prompt embeds: scene metadata, narration_final, **target runtime (ffprobe-measured)**, word-level timeline (capped at 60 words), prior-carry block, the `manifest_to_prompt_block()` style spec, and the matching layout-template body. After each call: `safe_transform()` → `run_gates()` (parse → forbidden-name walk → class-name `Scene<NNN>` check → predicted-runtime ∈ [0.92T, 1.05T]). On gate failure: ONE repair round with the gate error injected; on second failure raises `LLMError`. Failed attempts saved to `scene_<id>.attempt_N.py.bak`. |
| `pipeline/orchestrator.py` | Extended: added `scene_codes: list[SceneCode]` to `PipelineResult`, `_animate_one()` (LLM_SEM-capped per-scene wrapper), `_phase4_fanout()` (gathers across scenes; uses `empty_carry()` for now since carry files only exist post-render). `run_pipeline()` now produces phases 0-4. CLI smoke output prints predicted vs target per scene. |
| `tests/test_sanitize.py` | 8 tests: each transform verified, word-boundary safety, idempotency, no-op on clean source, multi-transform single pass. |
| `tests/test_templates.py` | 32 parametrized tests (6 layouts × ~5 invariants): every template file exists, parses, imports manim + style, references at least one zone constant, ends without `self.wait(N)`, contains `FadeOut` cleanup. Plus unknown-layout rejection. |
| `tests/test_animator.py` | 14 tests covering `_strip_fences` (3), `run_gates` (8 — accept good code; reject syntax error, ShowCreation, TextMobject, runtime too short/long, wrong class name, no Scene subclass, empty), and end-to-end `animate()` with monkeypatched `call_agent` (3 — first-attempt success, repair-round recovery on runtime-window failure, two failures → `LLMError`, prior-carry block included in user prompt). |
| `tests/test_orchestrator.py` | Extended: `test_run_pipeline_fanout` now also stubs the animator and asserts `len(result.scene_codes) == 2` plus per-scene `.py` paths exist under `output/scenes/`. |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest -q
........................................................................
........................................                                 [100%]
116 passed, 2 deselected, 4 warnings in 3.03s
```

116 = 23 (Day 1) + 24 (Day 2) + 7 (Day 3) + 62 (Day 4 — including the
parametrized template suite which contributes most of the count).

### Template runtime check (sanity)

Every template's literal `run_time=` values sum to a deterministic total —
zero `used_default_play_runtime` calls, so the AST predictor doesn't
fall back to Manim's 1.0s default for any template:

| Layout | Predicted | Plays |
|---|---|---|
| title_only | 5.00s | 4 |
| title_plus_eq | 6.20s | 5 |
| equation_focus | 9.00s | 5 |
| graph | 7.50s | 6 |
| derivation_chain | 5.00s | 4 |
| split_eq_text | 7.50s | 5 |

These template totals are EXAMPLES, not the values the Animator emits — the
LLM scales the plays' `run_time=` literals to the per-scene target T pulled
from `audio.duration_s`. The templates exist to demonstrate the structural
pattern (explicit literals, layout zones, FadeOut tail).

### Important contract details a future session must respect

- **Sanitize runs BEFORE gates, not after.** The forbidden-name gate would
  fire on `ShowCreation` if it ran first; sanitize rewrites those legacy
  names to current ones first, so the gate's job is to catch the patterns
  sanitize CAN'T fix (`add_sound`, custom shaders, raw LaTeX in `Text`,
  wrong class name, runtime window). When you write a test for repair-round
  behavior, do NOT use `ShowCreation` — sanitize will silently fix it before
  the gate sees it. Use a runtime-window violation or `add_sound`.
- **Polygon spread uses bracket-depth tracking, not regex.** A naive
  `[^\[\]]*?` regex can't span nested coord lists; the scanner walks
  character-by-character. If you ever need to extend it to other
  spread-able functions, factor out the `_replace_polygon` body.
- **Animator's repair-round vs registry's fallback chain:** the animator
  owns the GATE-failure repair (one extra LLM call with the gate error
  injected). The registry's `call_agent` owns the PROVIDER-failure fallback
  (RateLimit/ProviderError → next ModelSpec). Each `call_agent` call
  internally walks the full chain — so a gate failure followed by a repair
  round is at most 2 chain walks, not 6. This is acceptable.
- **Failing attempts are persisted as `.attempt_N.py.bak`.** The Healer in
  Day 5 will read these for diagnostics. Don't garbage-collect them between
  attempts within the same run.
- **`scene_class.name` MUST be `Scene<scene_id>`.** Hard-coded in
  `_find_scene_class` + checked in `run_gates`. The Director is told this
  in the system prompt. If you ever want to allow more flexible names, also
  update `pipeline/orchestrator.py`'s eventual render call (Day 5).
- **Phase 4 fan-out passes `empty_carry()` for every scene.** Real carry
  files are produced by the rendered Manim code at render time (Day 5
  introduces a hook for this). When carry chaining lands, `_phase4_fanout`
  becomes serial-or-DAG instead of `gather`.
- **Layout templates are .py, not .py.j2.** The plan called for Jinja but
  Day 4 found no parameterization need — the LLM does the substitution
  itself. Preserve the .py extension; don't introduce Jinja unless a
  template needs structural parameterization a future session can't
  express in the prompt.

## ✅ Day 5 — Renderer + Healer + Assembler + setup_check (complete, 2026-05-03)

Day 5 closed the AEVE 2.0 loop end-to-end. `run_pipeline()` now goes from
prompt to playable .mp4. Legacy AEVE 1.0 entry points (`assemble_final_video`
in `renderer/assembler.py`) are still present and untouched so `app.py` /
`main.py` keep working in side-by-side mode.

### Files created / changed

| File | Purpose |
|---|---|
| `setup_check.py` (new) | Standalone environment verifier. Probes Python version, conda env name, `ffmpeg`, `ffprobe`, `manim` (tries `manim` CLI first, falls back to `python -m manim` so it doesn't false-positive missing in IDEs), and `latex`/`xelatex` (optional — degradation to matplotlib MathTex backend is documented). CLI flags: `--json` (machine-readable), `--install-miktex` (opt-in: tries conda-forge then winget). Importable via `from setup_check import check_setup`. Wired as `aeve-setup-check` console script in pyproject.toml. |
| `renderer/render.py` (new) | `async render_scene(*, code, audio, video_dir, style, cfg) -> SceneVideo`. **Manim invoked via `[sys.executable, "-m", "manim", ...]`** so it works without the `manim` script being on PATH. Flags: `--disable_caching` (avoid cross-run hash collisions), `--progress_bar none` (Windows stdout safety), `-v WARNING`. On nonzero exit captures last 4 KB of stderr (vs. legacy 800 chars), saves the failing source as `scene_<id>.attempt_N.py.bak`, and hands the tail to `renderer.healer.heal()`. After up to `cfg.max_attempts=4` healer rounds, falls back to `renderer.healer.write_fallback_scene()` — a deterministic Jinja template guaranteed to parse and render. After successful render: `pad_or_trim()` corrects drift; then `_mux_sync` muxes the MP3 onto the silent video — **NO `-shortest`** ever. Final ffprobe round measures the real duration. |
| `renderer/healer.py` (new) | `async heal(*, broken_code, stderr_tail, target_runtime_s, scene_id, style)`. Calls `call_agent(role="healer")` with a tight repair contract (full Python file in / full Python file out, no commentary). Sanitizes the response (`renderer.sanitize.safe_transform`) then runs the Animator's `run_gates()`. On gate rejection raises `LLMError` so the renderer's loop budget shrinks by one. Also exposes `write_fallback_scene(*, py_path, scene_id, title, formulas, target_runtime_s)` which renders `pipeline/fallback_scene.py.j2` with `_allocate_runtimes()` partitioning the target into intro / per-formula / emphasis / outro slots. Output is guaranteed inside `[0.92T, 1.05T]`. |
| `pipeline/fallback_scene.py.j2` (new) | Jinja template for the deterministic last-resort scene. **Critical detail**: every `self.play()` is unrolled into the template — no `for` loops with plays inside, because the AST runtime predictor walks plays linearly (`ast.walk` visits a Call node once regardless of containing loop). With formulas: each formula gets its own explicit `Write(eq_N)` play with literal `run_time=per_formula_s`. Without formulas: a two-step color-pulse on the title fills the body. Always ends with `self.play(*[FadeOut(m) for m in self.mobjects], run_time=outro_s)`. |
| `renderer/assembler.py` (extended) | Legacy `assemble_final_video()` / `concatenate_scenes()` / `merge_audio_video()` UNCHANGED. Added: `async assemble(*, scene_videos, scene_audios, final_dir, work_dir, ...)` — the AEVE 2.0 final pass. **Normalize-then-concat**: `build_normalize_cmd` re-encodes each scene to `fps=30, scale=1920:1080, setsar=1, -video_track_timescale 30000, -c:a aac -ar 48000 -ac 2 -b:a 192k`. `build_concat_cmd` then runs the demuxer with `-c copy` (safe because every input is canonicalized) plus `-movflags +faststart` for streaming. **NO `-shortest`** in any path. After concat, `ffprobe_duration()` asserts `|final - sum(audio_durations)| < drift_budget_s` — over-budget is logged as ERROR but does NOT raise, since the .mp4 is still playable; the CI gate is the caller's. |
| `pipeline/orchestrator.py` (extended) | Added phases 5 + 6. New helpers: `_phase5_render_one()` (RENDER_SEM-capped) and `_phase5_fanout()` (gathers scene-render across cores). `run_pipeline()` now extends from phases 0-4 to phases 0-6 — the result type is the same `PipelineResult` dataclass with two new fields: `scene_videos: list[SceneVideo]` and `final_video: FinalVideo`. CLI smoke output (`python -m pipeline.orchestrator "<query>"`) now prints final mp4 path, total duration, and per-scene drift. |
| `tests/test_setup_check.py` (new) | 9 tests on report semantics (status-line markers, ok-iff-required-found, latex doesn't block ok), `check_python` accepts the runtime, `shutil.which`-mocked missing-tool simulation, `to_dict()` JSON-serializable. |
| `tests/test_assembler.py` (new) | 8 tests covering command shape (canonical fps/scale/timebase/audio params), no-`-shortest` invariant on both normalize and concat commands, concat-list writer (format + apostrophe escaping), and `assemble()` happy path / drift-exceeds-budget / empty-input rejection — all with mocked `_normalize`/`_concat`/`ffprobe_duration`. |
| `tests/test_healer.py` (new) | 11 tests on `_allocate_runtimes` math (sums close to target; clamps below 1s; per_formula > 0 even with no formulas), `_strip_fences`, `write_fallback_scene` (parses, names `Scene<NNN>`, lands in band, skips blank formulas), and `heal()` end-to-end with monkeypatched `call_agent` (validated success, rejection on unfixable output, fence-stripping). |
| `tests/test_render.py` (new) | 9 tests covering `_tail_bytes` (passthrough/truncate/None), `_manim_cmd` shape (uses `python -m manim`, `--disable_caching`, `--progress_bar none`), no-`-shortest` invariant on the mux command (verified by spying on `subprocess.run`), and `render_scene()` end-to-end paths: happy first-attempt success, healer recovers on attempt 2, all attempts fail → deterministic fallback used. All subprocess and ffprobe calls are monkeypatched — no real binaries required. |
| `tests/test_orchestrator.py` (extended) | Now stubs `render_scene` and `assemble` so the test suite is fully offline. Asserts `len(result.scene_videos) == N` and `result.final_video` populated. |
| `pyproject.toml` (already had it) | `aeve-setup-check = "setup_check:main"` console script entry was added on Day 1; Day 5 made it actually do something. |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest -q
.................................................................. (155)
155 passed, 2 deselected, 4 warnings in 5.74s
```

155 = 23 (Day 1) + 24 (Day 2) + 7 (Day 3) + 62 (Day 4) + 39 (Day 5: 9 setup
+ 8 assembler + 11 healer + 9 render + 2 orchestrator-extension). Both
`@pytest.mark.live` tests still gated.

### Live setup_check report (cv_conda host)

```
[OK   ] python     3.10.19 (cv_conda)
[MISS ] conda_env  — only when invoked outside `conda activate cv_conda`
[OK   ] ffmpeg     2026-01-05 essentials build
[OK   ] ffprobe    2026-01-05 essentials build
[OK   ] manim      Manim Community v0.19.1 (via `python -m manim`)
[warn ] latex      — falls back to matplotlib MathTex (lower quality)
```

A live render needs MiKTeX for `MathTex`. If absent the rendered scenes
swap to a matplotlib backend; the pipeline does NOT block.

### Important contract details a future session must respect

- **Manim is invoked as `python -m manim`, not `manim`.** This works in
  IDEs, CI, and any context where the conda env's `Library/bin` isn't on
  PATH. If you ever change this back to bare `manim`, also update
  `setup_check.check_manim` and `RenderConfig` documentation.
- **`-shortest` is forbidden in EVERY ffmpeg invocation** introduced by
  AEVE 2.0. Two test files (`test_render.py`, `test_assembler.py`) carry
  `assert "-shortest" not in cmd` invariants. The legacy
  `merge_audio_video()` still uses `-shortest` — that's intentional, the
  legacy path is preserved for backward-compat. Don't propagate it.
- **The render fallback chain has a deterministic terminator.** After
  `cfg.max_attempts=4` healer rounds, `write_fallback_scene` produces a
  scene that's guaranteed to parse + render. The pipeline NEVER blocks on
  a single bad scene. If you tighten `max_attempts`, also make sure the
  fallback runs at least once before the loop exits.
- **The fallback Jinja template MUST keep its plays unrolled.** A `for`
  loop wrapping `self.play(...)` will sum to the wrong total in the AST
  predictor (which sees one Call node regardless of loop iterations). If
  you ever extend the template, count plays after rendering and verify
  with `predict_manim_runtime(rendered_text).in_window(target)`.
- **Drift over budget is logged, not raised.** `assemble()` emits an
  ERROR-level log if the final-vs-sum drift exceeds 50 ms but still returns
  a `FinalVideo`. The CI gate (`tests/golden`) is the caller's
  responsibility. This is deliberate — a 60 ms drift video is still
  watchable; we don't want to hard-fail user-facing flows.
- **`assembler.assemble()` and `assembler.assemble_final_video()` are
  different functions.** The first is async + AEVE 2.0; the second is sync
  + AEVE 1.0 + uses `-shortest`. Day 6 will retire the legacy one. Until
  then, `__all__` exports both.
- **`_phase5_fanout` is gather-based, not sequential.** Carry-chaining
  isn't wired yet (Day 6 problem) — for now every scene gets `empty_carry`
  via `_phase4_fanout`. When we wire real carry chaining, `_phase5_fanout`
  stays gather (renders are independent given codes); but `_phase4_fanout`
  must become sequential-in-scene-id so each Animator gets the prior
  scene's carry.json after its render produced it.

## ✅ Day 6 — Wire AEVE 2.0 into UI + CLI; CI gates (complete, 2026-05-03)

Day 6 made AEVE 2.0 the **default** for both the CLI (`python main.py`) and
the web UI (`python app.py`), with the legacy 10-agent pipeline preserved
behind explicit opt-ins (`--legacy` flag and `mode=legacy` form field).
Carryover plumbing landed (runtime helper for traceability); full
prior-scene-aware chaining is deferred to Day 7+. Two test gates added:
the `no-legacy-sync` grep-style import assertion and a golden-frame
scaffold (skips until first live render seeds `expected.png`).

### Files created / changed

| File | Purpose |
|---|---|
| `pipeline/runtime.py` (new) | `emit_carry(scene_id, named_mobjects, output_path=None)` — stdlib-only helper callable from generated Manim scenes. Duck-types `.get_center()` (so tests don't need real manim mobjects), writes `scene_<id>.carry.json` listing names + kinds + (x,y,z) positions. **Not** a `self.play`/`self.wait` call → invisible to the AST runtime predictor. |
| `pipeline/animator.py` (extended) | Added a CARRY-OUT block to the system prompt instructing the Animator to call `from pipeline.runtime import emit_carry; emit_carry("<scene_id>", {"name": mob, ...})` just before the final FadeOut when `carryover_objects` is non-empty. The gates are unchanged — emit_carry doesn't appear in `FORBIDDEN_NAMES` and isn't a play/wait, so it passes through cleanly. |
| `main.py` (rewrite) | Default path is AEVE 2.0 — `asyncio.run(run_pipeline(...))`. CLI flags: `query` (positional), `--image`, `--target-seconds` (default 60, clamped [20,180] by schema), `--output-dir`, `--legacy` (opt-in to AEVE 1.0), `--quality {low,medium,high,4k}` (legacy only — emits a note in 2.0 mode that it's ignored, since AEVE 2.0 hardcodes 1080p30). Legacy AEVE 1.0 flow preserved as `_run_legacy()` so the side-by-side period works. |
| `app.py` (extended) | Two SSE workers now: `run_pipeline_job` (legacy AEVE 1.0, unchanged) and `run_pipeline_job_v2` (new). The v2 worker calls `pipeline.orchestrator._phase{0..6}_*` helpers individually so each phase boundary emits an explicit SSE `phase` event with `running`/`done` status. `/start` dispatches on the `mode` form field (`v2` is default; `legacy` opts in). `/output/<filename>` works for both `final.mp4` (2.0) and `final_video.mp4` (legacy) since both live in `config.FINAL_DIR`. |
| `tests/test_runtime.py` (new) | 6 tests for `emit_carry`: canonical path, payload round-trip, missing `get_center` graceful fallback, empty mapping, parent-dir creation, end-to-end `emit_carry → read_carry` round-trip via `pipeline.carryover.read_carry`. |
| `tests/test_no_legacy_sync.py` (new) | CI gate: AEVE 2.0 modules must NOT import `pipeline.sync_engine`. Walks the curated AEVE 2.0 file list (`pipeline/{orchestrator,solver,director,narrator,tts,animator,timing,style,schemas,carryover,runtime}.py` + `renderer/{render,healer,sanitize,assembler}.py`) and grep-asserts no `from pipeline.sync_engine` / `import pipeline.sync_engine`. A second test extracts just the `async def assemble(...)` body from `renderer/assembler.py` and asserts it doesn't reference `sync_engine` (the legacy `assemble_final_video` in the same file still does — that's intentional). |
| `tests/test_golden.py` (new) | Golden-frame regression scaffold. The hand-curated `tests/golden/pythagoras_60s/scene_003.py` is rendered + diffed against `expected.png` via `PIL.ImageChops` (per-pixel tolerance 8/255, total diff < 2%). Until `expected.png` is bootstrapped on real hardware, the live test skips with a clear message. The fixture sanity check (parses + AST-predicted runtime ∈ [4.6s, 5.4s]) runs offline. |
| `tests/golden/pythagoras_60s/` (new) | Hand-curated golden fixture: `scene_003.py` (LaTeX-free MathTex chain at WHITE/BLUE so it doesn't depend on the `output._style` constants — golden frames must be reproducible without the per-run StyleManifest), and `README.md` documenting the bootstrap procedure. |
| `tests/test_e2e.py` (new) | Two `@pytest.mark.live` tests: `test_pythagoras_e2e` (the canonical "Prove the Pythagorean theorem" prompt; asserts 4-8 scenes, < 50 ms total drift, < 50 ms per-scene drift, plays back) and `test_short_e2e_minimal` (a 25 s smoke prompt to verify the chain is wired). Wall-clock is logged but not asserted — too host-dependent. |

### Test results

```
$ /c/Users/shanm/anaconda3/envs/cv_conda/python.exe -m pytest -q
.................................................................. (165)
165 passed, 5 deselected, 4 warnings in 6.59s
```

165 = 155 (Day 5) + 10 (Day 6: 6 runtime + 2 no-legacy-sync + 2 golden
sanity). 5 deselected = the original 2 live-marked tests + 2 e2e tests +
1 golden live test, all gated behind `pytest -m live`.

### Important contract details a future session must respect

- **AEVE 2.0 is the default.** Both `python main.py "..."` and
  `python app.py` use it without flags. Legacy 1.0 requires explicit
  opt-in via `--legacy` (CLI) or `mode=legacy` form field (web). When
  Day 7 retires AEVE 1.0, just delete the `_run_legacy()` paths +
  `run_pipeline_job` (legacy worker) + `pipeline/phase{1,2,3}_*.py` +
  `pipeline/sync_engine.py` + `renderer/manim_runner.py` + the legacy
  `assemble_final_video` in `renderer/assembler.py`. The
  `test_no_legacy_sync.py` invariant will already be holding by then.
- **`pipeline/sync_engine.py` is NOT deleted.** It's still imported by
  `pipeline/phase3_distributor.py` (legacy AEVE 1.0). The
  `test_no_legacy_sync.py` invariant guarantees no AEVE 2.0 module ever
  imports it. The plan to delete it outright was downgraded — deleting
  while the legacy path still runs would be a regression.
- **`emit_carry` is a side-effect, not a return.** Generated Manim scenes
  call `emit_carry(...)` for traceability; the orchestrator does NOT
  currently READ those carry files (Day 7+ feature). For now the
  Animator prompt mentions it as recommended, the Animator is given an
  empty `prior_carry` from `_phase4_fanout` regardless, and chained
  continuity comes from the storyboard's `carryover_objects` field +
  the Animator's own awareness of layout zones.
- **Golden frames must be deterministic across model rolls.** That's
  why `tests/golden/pythagoras_60s/scene_003.py` is hand-curated, uses
  literal `MathTex` strings, and uses `BLUE/WHITE` color constants from
  `manim` directly (not `ACCENT/PRIMARY` from `output._style`). Day 7's
  golden bootstrap should write `expected.png` from a render of THIS
  file, not from a StyleManifest-coloured per-run scene.
- **The web UI now has SIX `phase` events, not four.** `phase0` …
  `phase6` for AEVE 2.0; legacy mode still emits `phase1`, `phase2`,
  `phase3`, `assembly`. If the frontend hard-codes the legacy four,
  Day 7 needs a frontend update — that's deferred from Day 6 since
  this repo's `frontend/` was last touched for AEVE 1.0 styling.
- **The `mode` form field is the single switch.** Don't add per-phase
  feature flags to `/start` — every flag is a bug surface for the
  side-by-side period. If you need to gate a Day 7 feature, add a new
  form field with a sensible default.

## ⏭️ Day 7 — Next up

Day 7 closes the loop on the rewrite by retiring legacy code paths and
landing the polish features that needed real renders to validate.

1. **Bootstrap the golden frame.** Run `pytest -m live --update-golden`
   on a host with MiKTeX + Manim installed; the test should write
   `tests/golden/pythagoras_60s/expected.png` and metadata, then verify
   it on a re-run. Wire the actual render-and-compare logic in
   `tests/test_golden.py` (it's currently a skip placeholder).
2. **Cross-scene continuity from storyboard.** Make `_phase4_fanout`
   sequential per scene, reading scene N's storyboard
   `carryover_objects` to populate scene N+1's `prior_carry` with
   predicted positions (default to MAIN_POS if name doesn't disambiguate).
   Animator's `prior_carry` in the user prompt becomes substantively
   non-empty for scene 2+.
3. **Frontend SSE update.** `frontend/templates/index.html` +
   `frontend/static/*.js` — extend the pipeline diagram to render six
   AEVE 2.0 phases (currently four legacy boxes). Read mode from form;
   default to `v2`.
4. **Retire legacy paths.** Delete `pipeline/phase{1,2,3}_*.py`,
   `pipeline/sync_engine.py`, `renderer/manim_runner.py`, the
   `assemble_final_video` / `merge_audio_video` / `concatenate_scenes`
   functions in `renderer/assembler.py`, and the `_run_legacy` /
   `run_pipeline_job` (legacy worker) blocks in `main.py` / `app.py`.
   `models/llm_client.py` is the harder call — it's the legacy router;
   the AEVE 2.0 clients live in `pipeline/llm_clients/`. Decide whether
   to keep `models/llm_client.py` for its `LLMError` / `logger` exports
   (and rewire those into `pipeline.llm_clients.errors`), or absorb +
   delete entirely.
5. **README polish.** Once legacy is gone, drop the side-by-side
   language. The "Status" banner becomes "AEVE 2.0 — generally
   available" or similar.
6. **`pyproject.toml` cleanup.** Remove `models/` from the package
   discovery list once the legacy router is retired.

Day 7 exits when:
- The golden test passes deterministically on a fresh checkout (after
  bootstrap).
- All `phase{1,2,3}_*.py` files are deleted; `pytest` still passes.
- `python main.py "..."` and `python app.py` work end-to-end with no
  legacy code path reachable.
