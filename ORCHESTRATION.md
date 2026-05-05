# AEVE Orchestration — agent contracts

This is the authoritative spec for the AEVE 2.0 agent pipeline. For each
phase: input schema → agent → output schema, what gates the boundary, what
the system prompt enforces, and what happens on failure.

For the user-facing intro see `README.md`. For the internal change log
and contract gotchas see `CLAUDE.md`.

> **Status (2026-05-03):** AEVE 2.0 has 5 LLM-backed agents (Solver,
> Director, Narrator, Animator, Healer) plus one non-LLM service (TTS via
> edge-tts). The legacy 10-agent pipeline (M1-M10) is preserved for
> backward compatibility but is documented at the bottom of this file
> only — every new feature targets AEVE 2.0.

---

## Pipeline shape

```
Phase 0  StyleManifest         deterministic Python (no LLM)
Phase 1  Solver       (S)      DeepSolution     ← user query
Phase 2  Director     (D)      Storyboard       ← DeepSolution
Phase 3  Narrator+TTS (N+E)    SceneAudio[]     ← StoryboardScene (per-scene)
Phase 4  Animator     (A)      SceneCode[]      ← StoryboardScene + SceneAudio + prior carry
Phase 5  Render+Healer (R+H)   SceneVideo[]     ← SceneCode + SceneAudio
Phase 6  Assembler             FinalVideo       ← SceneVideo[] + SceneAudio[]
```

Concurrency:

* `LLM_SEM = asyncio.Semaphore(4)` caps Narrator / Animator / Healer LLM
  calls per scene fan-out.
* `RENDER_SEM = asyncio.Semaphore(2)` caps the manim+ffmpeg subprocess
  fan-out.
* Within a scene: serial (Narrator → TTS → Animator → Render).
* Across scenes: gathered via `asyncio.gather(...)`.

---

## Model routing

Every LLM-backed agent has **a primary spec plus two fallbacks**. Fallback
triggers: HTTP 429, 5xx, timeout, schema validation failure twice in a
row. The chain is hard-wired in `pipeline/llm_clients/registry.py`:

| Agent | Primary | Fallback 1 | Fallback 2 | Temperature |
|---|---|---|---|---|
| **Solver (S)** | Groq `moonshotai/kimi-k2-instruct` | OpenRouter `deepseek/deepseek-chat-v3` | Groq `llama-3.3-70b-versatile` | 0.2 |
| **Director (D)** | Groq `llama-3.3-70b-versatile` | OpenRouter `meta-llama/llama-3.3-70b-instruct` | Groq `llama-3.1-8b-instant` | 0.4 |
| **Narrator (N)** | Groq `llama-3.3-70b-versatile` | OpenRouter `zai/glm-4.6` | Groq `llama-3.1-8b-instant` | 0.5 |
| **TTS (E)** | edge-tts `en-US-AriaNeural` | edge-tts `en-US-JennyNeural` | edge-tts `en-US-GuyNeural` | n/a |
| **Animator (A)** | OpenRouter `nvidia/nemotron-3-coder` | OpenRouter `qwen/qwen3-coder` | OpenRouter `deepseek/deepseek-chat-v3` | 0.2 |
| **Healer (H)** | OpenRouter `deepseek/deepseek-r1` | OpenRouter `nvidia/nemotron-3-coder` | OpenRouter `qwen/qwen3-coder` | 0.1 |

OpenRouter slugs are *intent*. The client probes `/models` at startup and
remaps to the latest variant of the family if the exact slug shifts (e.g.
`nvidia/nemotron-3-coder` → `nvidia/nemotron-nano-9b-v2:free`). If the
entire family is gone the registry advances to the next spec.

Groq's per-request key rotation across `config.GROQ_API_KEYS` handles 429s
inside a single spec before the chain advances.

---

## Phase 0 — StyleManifest (deterministic)

**Module:** `pipeline/style.py` · **No LLM.**

Build once, before any agent runs. Inputs: difficulty (default
`"intermediate"`), palette name (default `"blue_yellow_dark"`), font.
Outputs:

* `output/style_manifest.json` — JSON spec, embedded verbatim into every
  Director / Narrator / Animator prompt.
* `output/_style.py` — Python module with palette + layout constants;
  every generated Manim scene imports this with
  `from output._style import *`.

Schema: `pipeline.schemas.StyleManifest`. Six required palette keys (`bg`,
`primary`, `accent`, `muted`, `success`, `warn`), all hex. Six layout
zones (`title`, `main`, `caption`, `left_rail`, `right_rail`, `footer`).

---

## Phase 1 — Solver

**Module:** `pipeline/solver.py` · **Role:** `solver` · **Function:**
`async solve(query, *, image_hint=None) → DeepSolution`

### System prompt enforces

* Output ONLY a JSON object matching the `DeepSolution` schema. No prose,
  no markdown fences, no commentary.
* Raw LaTeX in `latex` fields (no `$…$` delimiters). Required: `topic`,
  `difficulty` (one of `intro`/`intermediate`/`advanced`), `prerequisites`
  (may be empty), `steps` (≥ 1, each with `narrative`, `latex|null`,
  `visual_intent`), `conclusion`.
* `narrative` and `visual_intent` are concise — these become voiceover
  and animation hints downstream.

### Robustness

`call_agent_json(role="solver", parser=_parse)`:

1. Strips ```` ```json ```` fences.
2. Validates against `DeepSolution`.
3. Best-effort unwrap: if the top level is a single-key dict
   (`{"response": {...}}`), unwrap once before re-validating.
4. On parser failure: ONE repair round per spec with the validation error
   injected. Then advance to the next spec in the chain.
5. After all specs exhausted: raises `LLMError`.

---

## Phase 2 — Director

**Module:** `pipeline/director.py` · **Role:** `director` · **Function:**
`async direct(solution, *, target_seconds=60, style=None) → Storyboard`

### System prompt enforces

* Output ONLY a JSON object matching `Storyboard` schema. No prose, no
  fences.
* `total_target_seconds` ≈ `target_seconds`, clamped to `[20, 180]`.
* 4-7 scenes typical, never more than 10.
* Each scene's `narration_draft` ≤ 2 sentences; math expressed in raw
  LaTeX (no `$$`).
* `layout` ∈ `{title_only, title_plus_eq, equation_focus, graph,
  derivation_chain, split_eq_text}` — the Director picks; the Animator
  never invents layouts.
* `transition_in` ∈ `{fade, slide_left, none}`.
* `scene_id` zero-padded ascending: `001`, `002`, …
* Reuse `carryover_objects` to morph one scene into the next instead of
  cutting hard.

If a `StyleManifest` is supplied, its JSON form is included in the user
prompt so the Director keeps its plan consistent with the visual contract.

### Robustness

Same `call_agent_json` parser pattern as Solver. Validators in `Storyboard`
reject duplicate scene_ids, unsorted scene_ids, palettes missing keys, etc.

---

## Phase 3 — Narrator + TTS

Two sub-phases inside one per-scene flow.

### Phase 3a — Narrator

**Module:** `pipeline/narrator.py` · **Role:** `narrator` · **Function:**
`async polish(scene) → str`

Plain-text output (uses `call_agent`, not `call_agent_json`). Expands LaTeX
into spoken English so edge-tts can pronounce it correctly:

| LaTeX | Spoken |
|---|---|
| `a^2` | "a squared" |
| `\frac{a}{b}` | "a over b" |
| `\sqrt{x}` | "the square root of x" |
| `\pi` | "pi" |
| `=` | "equals" |

### System prompt enforces

* ONE block of plain prose, no JSON / markdown / SSML / quotes.
* AT MOST 2 sentences and 60 words.
* Natural cadence; contractions allowed; no formal proof phrasing.
* Never start with "In this scene".
* Never include LaTeX source or backslashes.

### Output validator (`_validate`)

* Strips matched surrounding quotes if present.
* Hard-truncates at the nearest sentence boundary if `len > 400`.
* Rejects any LaTeX residue: `\command`, `$`, `^`, `_{`, `\frac`, `\sqrt`.
  This forces the LLM to actually convert math to words.

### Phase 3b — TTS (no LLM)

**Module:** `pipeline/tts.py` · **Function:** `async synthesize(*, text,
out_path, scene_id, voice, rate, volume) → SceneAudio`

Streams `edge_tts.Communicate.stream()`. Two simultaneous outputs:

1. MP3 audio chunks → written incrementally to disk.
2. `WordBoundary` chunks → `list[WordEvent]` with `start_s` / `end_s`
   (HNS / 1e7 = seconds).

Voice fallback chain: `Aria → Jenny → Guy`. Walks on any exception
(WebSocket disconnect, rate limit, NoAudioReceived, …).

After streaming, `ffprobe_duration()` is the authoritative duration. If
WordBoundary was empty (rare), `_synthetic_timeline()` fabricates a uniform
per-word split so the Animator still has anchors.

Output: `SceneAudio` with `mp3_path`, `duration_s`, `word_timeline`,
`narration_final`.

---

## Phase 4 — Animator

**Module:** `pipeline/animator.py` · **Role:** `animator` · **Function:**
`async animate(*, scene, audio, prior_carry, style, scenes_dir) →
SceneCode`

Generates a complete `.py` Manim file whose total runtime lands within
`[0.92T, 1.05T]` of the audio's measured duration `T`.

### System prompt enforces

* Imports: `from manim import *` and `from output._style import *`.
* Allowed primitives: `Text, MathTex, Tex, MarkupText, VGroup, Axes,
  NumberPlane, FunctionGraph, Arrow, Dot, Line, Circle, Square, Rectangle,
  RoundedRectangle, Polygon, BraceLabel, SurroundingRectangle, Code`.
* Forbidden: `ShowCreation`, `TextMobject`, `TexMobject`, `add_sound`,
  custom shaders, third-party imports, `Polygon([list])` (must spread),
  raw LaTeX inside `Text(...)` (use `MathTex`).
* **Layout discipline:** every VMobject anchored at one of the layout
  zones (`TITLE_POS`, `MAIN_POS`, `CAPTION_POS`, `LEFT_RAIL_POS`,
  `RIGHT_RAIL_POS`, `FOOTER_POS`) imported from `output._style`. Never
  invent free-floating coordinates.
* **Color palette:** only `BG, PRIMARY, ACCENT, MUTED, SUCCESS, WARN`.
* **Pacing budget:** ~10% intro / ~70% derivation / ~15% emphasis / ~5%
  transition out. Every `self.play(...)` must include a literal
  `run_time=<float>` (no variable-driven values — the AST predictor needs
  literals).
* **Required ending:** `self.play(*[FadeOut(m) for m in self.mobjects],
  run_time=0.5)`. NO trailing `self.wait(N)`.
* **Continuity:** prior scene's `SceneCarry` is included in the prompt;
  reused formulas should `ReplacementTransform` from the prior form.
* Class name MUST be `Scene<NNN>` where `NNN` is the zero-padded scene_id.

### User prompt includes

* Scene metadata + narration final + word-level timeline (capped at 60
  words) + target runtime + acceptable AST-predicted band.
* Prior `SceneCarry` block.
* `StyleManifest` JSON.
* The matching layout-template body (`pipeline/templates/<layout>.py`)
  embedded as a structural reference.

### Gates (after `renderer.sanitize.safe_transform`)

1. **Parse** — `ast.parse(code)` raises `AnimatorGateError` on
   `SyntaxError` (mapped from `exc.lineno` + `exc.msg`).
2. **Forbidden-name walk** — rejects `ShowCreation`, `TextMobject`,
   `TexMobject`, `add_sound`. (Sanitize already auto-fixes the renames;
   the gate catches what the LLM kept anyway, like `add_sound`.)
3. **Class name** — must be `Scene<NNN>`.
4. **Predicted runtime** — `predict_manim_runtime(code).in_window(T,
   lo=0.92, hi=1.05)`.

### Robustness

ONE repair round on gate failure: the gate error is appended to the user
prompt and `call_agent` is re-invoked. After a second failure, raises
`LLMError`. Failed sources are saved as
`scene_<id>.attempt_N.py.bak` for diagnostics.

The Animator's repair-round vs. the registry's provider fallback chain are
two different mechanisms: `call_agent` walks the full provider chain
internally on `RateLimitError` / `ProviderError`. The Animator only owns
the gate-failure repair loop.

---

## Carryover (cross-scene continuity)

**Modules:** `pipeline/runtime.py` (writer) + `pipeline/carryover.py` (reader)

Each scene may declare a small JSON manifest of "objects that survive" so
scene N+1 can place them at their final positions before introducing new
ones. Two artifacts:

* **`pipeline.runtime.emit_carry(scene_id, named_mobjects, output_path=None)`**
  is callable from inside generated Manim scenes. It writes
  `output/scenes/scene_<id>.carry.json`. The Animator's system prompt
  instructs the LLM to call this just before the final `FadeOut` when the
  storyboard's `carryover_objects` list is non-empty.

* **`pipeline.carryover.read_carry(scenes_dir, scene_id) → SceneCarry`**
  is the reader. Returns an empty `SceneCarry` if the file is missing
  (the expected case for the very first scene of a video and the current
  default for every scene at Day 6 — full chaining is Day 7+).

The render-time emit is for **traceability + Day 7 chaining**. As of
Day 6, `_phase4_fanout` passes `empty_carry()` to every Animator call;
the cross-scene continuity comes purely from the storyboard's
`carryover_objects` field + the layout zones the Animator already knows.

`emit_carry` is **not** a `self.play` / `self.wait` call, so it doesn't
disturb the AST runtime predictor. The forbidden-name walker doesn't
flag it.

---

## Phase 5 — Render + Healer

**Modules:** `renderer/render.py` + `renderer/healer.py` · **Function:**
`async render_scene(*, code, audio, video_dir, style, cfg) → SceneVideo`

### Render loop

```
for attempt in range(cfg.max_attempts=4):
    try:
        rendered = await _run_manim(...)        # python -m manim, --disable_caching, --progress_bar none
        break
    except RenderError as exc:                  # nonzero rc OR missing output
        save scene_<id>.attempt_<N>.py.bak
        if attempt == max_attempts:
            py_path = write_fallback_scene(...)  # deterministic Jinja
            rendered = await _run_manim(...)     # guaranteed success
            break
        healed_text = await heal(broken_code, stderr_tail=last_4kb, ...)
        py_path.write_text(healed_text)         # next iteration retries
```

Manim is invoked as `[sys.executable, "-m", "manim", "render", ...]` so
PATH issues don't block anything.

### Drift correction

After successful render, `pipeline.timing.pad_or_trim()`:

* `|measured - target| ≤ 50ms` → noop.
* Video shorter → `ffmpeg -vf "tpad=stop_mode=clone:stop_duration=Δ"`.
* Video longer → `ffmpeg -t target_s` (hard cut).

### Audio mux

```
ffmpeg -y -i silent.mp4 -i scene.mp3 \
  -map 0:v -map 1:a -c:v copy \
  -c:a aac -b:a 192k -ar 48000 -ac 2 \
  scene_<id>.mp4
```

`-shortest` is forbidden in this codepath. The test
`tests/test_render.py::test_mux_never_uses_shortest` enforces the
invariant by spying on `subprocess.run`.

### Healer

**Role:** `healer` · **Function:** `async heal(*, broken_code,
stderr_tail, target_runtime_s, scene_id, style) → str`

Tight repair contract: full Python file in / full Python file out. No
markdown fences, no diff, no commentary. The system prompt enumerates the
same forbidden-name and runtime-window rules as the Animator.

Healer output runs through `safe_transform` then the same `run_gates()` the
Animator uses. Gate rejection → `LLMError` (the renderer's loop budget
shrinks by one).

### Deterministic fallback

`renderer.healer.write_fallback_scene(*, py_path, scene_id, title,
formulas, target_runtime_s)` renders `pipeline/fallback_scene.py.j2`. It
guarantees:

* Parses cleanly.
* Class name `Scene<NNN>`.
* All `self.play()` calls unrolled (no for loops with plays inside) — so
  the AST predictor sums them correctly.
* Predicted runtime in `[0.92T, 1.05T]` for the given target.

---

## Phase 6 — Assembler

**Module:** `renderer/assembler.py` · **Function:** `async assemble(*,
scene_videos, scene_audios, final_dir, work_dir, output_name='final.mp4',
fps=30, drift_budget_s=0.050) → FinalVideo`

### Normalize-then-concat

```python
build_normalize_cmd:
    -vf "fps=30,scale=1920:1080:flags=lanczos,setsar=1"
    -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p
    -video_track_timescale 30000
    -c:a aac -ar 48000 -ac 2 -b:a 192k

build_concat_cmd:
    -f concat -safe 0 -i concat.txt -c copy -movflags +faststart
```

Every input is canonicalized before the demuxer runs, so `-c copy` is safe
(eliminates the cross-scene drift compounding the audit identified). NO
`-shortest` in either command. `-movflags +faststart` puts the moov atom
at the front for instant streaming.

### Drift assertion

After concat, `ffprobe_duration(final.mp4)` must satisfy
`|measured - sum(audio_durations)| < drift_budget_s`. Over budget logs
ERROR but does NOT raise — the .mp4 is still playable; the CI gate is
the caller's. The test gate is in `tests/golden/`.

---

## Dispatch layer (CLI + Web)

AEVE 2.0 is the default everywhere; legacy AEVE 1.0 is opt-in.

### `python main.py` (CLI)

```
python main.py "<query>"
python main.py "<query>" --target-seconds 90 --output-dir ./run01
python main.py --image diagram.png "Explain this image"
```

Flags:

* `--target-seconds INT` — desired runtime (clamped to `[20, 180]` by the
  `Storyboard` schema).
* `--image PATH` — uploaded image; turned into an `image_hint` string
  inlined into the Solver's user prompt.
* `--output-dir PATH` — defaults to `config.OUTPUT_DIR`.

### `python app.py` (Web)

`POST /start` form fields:

* `query` — the math topic (required if no image).
* `image` — optional file upload.
* `target_seconds` — desired total runtime. Defaults to 60. Clamped to
  `[20, 180]`.

SSE event types emitted on `GET /events/<job_id>`:

* `phase` — `{phase: "phase0".."phase6", status: "running"|"done", title, detail, [scenes, preview]}`.
* `log` — every line from the AEVE logger (level + message).
* `complete` — `{video_path, absolute_path, duration_s, drift_ms}`.
* `error` — `{message, type}`.
* `end` — sentinel; the SSE generator closes after this.

`/output/<filename>` serves files from `config.FINAL_DIR`.

---

## Validation summary (CI gates)

1. **Schema round-trip** — `tests/test_schemas.py`: every Pydantic model
   serializes/deserializes losslessly.
2. **AST predictor** — `tests/test_timing.py`: `predict_manim_runtime`
   handles literals, arithmetic-on-literals, missing `run_time` defaults.
3. **Animator gate logic** — `tests/test_animator.py`: forbidden-name
   walk, class-name check, runtime-window band, repair-round recovery.
4. **Sanitize** — `tests/test_sanitize.py`: each safe transform +
   idempotency + word-boundary safety.
5. **Layout templates** — `tests/test_templates.py`: every template
   parses, imports manim + style, ends without trailing wait, contains
   FadeOut.
6. **Render orchestration** — `tests/test_render.py`: happy path, healer
   recovery, deterministic fallback, no `-shortest` in mux.
7. **Healer** — `tests/test_healer.py`: gate-validated success, rejection
   on unfixable output, fallback scene parses + lands in band.
8. **Assembler** — `tests/test_assembler.py`: no `-shortest` in normalize
   or concat commands, drift logged but not raised.
9. **Setup** — `tests/test_setup_check.py`: report semantics + per-tool
   probes.
10. **Orchestrator glue** — `tests/test_orchestrator.py`: phases 0-6
    wire correctly with all LLM/subprocess calls stubbed.
11. **Carryover runtime** — `tests/test_runtime.py`: `emit_carry`
    payload + duck-typing + missing `get_center` fallback.
12. **No-legacy-sync** — `tests/test_no_legacy_sync.py`: legacy files
    stay deleted; AEVE 2.0 modules must NOT import `pipeline.sync_engine`
    or anything from the retired `models/` package; `renderer/assembler.py`
    must not reference `sync_engine` anywhere.
13. **Golden frame (offline sanity)** — `tests/test_golden.py`:
    `tests/golden/pythagoras_60s/scene_003.py` parses + has
    AST-predicted runtime in `[4.6, 5.4]`. The actual frame compare is
    live-only and skips until `expected.png` is bootstrapped.
14. **Live end-to-end (gated)** — `tests/test_e2e.py` (mark `live`):
    "Prove the Pythagorean theorem" prompt produces 4-8 scenes, < 50 ms
    total drift, < 50 ms per-scene drift, playable `final.mp4`.

Run `pytest` for the offline suite (165 tests, ~7 s). Add `-m live` to
include the LLM/edge-tts/render probes (consumes API quota and runs
real Manim).

---

## Legacy AEVE 1.0 (retired)

The original 10-agent pipeline (M1–M10) was retired in Day 7 of the
rewrite. The following files are gone:

* `pipeline/phase{1,2,3}_*.py`
* `pipeline/sync_engine.py` (regex `self.wait()` patcher; the audit
  identified this as the sync bug)
* `pipeline/audio_stream.py`, `pipeline/code_stream.py`
* `renderer/manim_runner.py`
* the `assemble_final_video()` / `merge_audio_video()` /
  `concatenate_scenes()` functions in `renderer/assembler.py`
* the `models/` package (legacy LLM router; AEVE 2.0 uses
  `pipeline/llm_clients/` instead)
* the `--legacy` flag in `main.py` and the `mode=legacy` form field in
  `app.py`

`tests/test_no_legacy_sync.py` carries two CI gates:

1. None of those files may be recreated.
2. No AEVE 2.0 module may `import` from `pipeline.sync_engine` or the
   `models` package.

A new file resurrecting any legacy pattern fails the test loudly. To
spelunk the historical contract, `git log` is the source of truth.
