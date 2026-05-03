"""
AEVE Web Frontend — Flask + SSE.

By default `/start` runs the AEVE 2.0 pipeline (5 agents, 6 phases). Pass
`mode=legacy` in the form data to fall back to the AEVE 1.0 10-agent
pipeline during the side-by-side period.

SSE events emitted:
    {"type": "phase", "data": {"phase": "<id>", "status": "running"|"done", ...}}
    {"type": "log",   "data": {"level": "info|warning|error", "message": ...}}
    {"type": "complete", "data": {"video_path": "..."}}
    {"type": "error",  "data": {"message": ..., "type": ...}}
    {"type": "end",    "data": {}}
"""

import asyncio
import os
import sys
import json
import time
import queue
import threading
import uuid
from flask import Flask, render_template, request, jsonify, Response, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

app = Flask(__name__, template_folder="frontend/templates", static_folder="frontend/static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload
UPLOAD_DIR = os.path.join(config.OUTPUT_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Global event queues for SSE (job_id → queue)
event_queues = {}


def emit_event(job_id: str, event_type: str, data: dict):
    """Push an event to the SSE queue for a job."""
    if job_id in event_queues:
        event_queues[job_id].put({
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        })


def patched_logger_emit(job_id):
    """Create a custom log handler that pushes events to SSE."""
    import logging

    class SSEHandler(logging.Handler):
        def emit(self, record):
            msg = self.format(record)
            level = record.levelname.lower()
            emit_event(job_id, "log", {"level": level, "message": msg})

    return SSEHandler()


# ---------------------------------------------------------------------------
# AEVE 2.0 worker — calls each phase individually so SSE can show progress.
# ---------------------------------------------------------------------------


async def _run_aeve2_with_events(
    job_id: str,
    query: str,
    *,
    image_path: str | None,
    target_seconds: int,
):
    """Drive AEVE 2.0 phases 0-6 with explicit SSE phase events.

    This duplicates a small amount of orchestrator wiring so the UI can
    show running/done per phase. The actual phase functions are imported
    from `pipeline.orchestrator`; we don't reimplement them.
    """
    from pathlib import Path

    from pipeline import orchestrator
    from pipeline.director import direct
    from pipeline.solver import solve
    from pipeline.style import build_style_manifest, write_style_artifacts
    from renderer.assembler import assemble

    out = Path(config.OUTPUT_DIR)
    audio_dir = out / "audio"
    scenes_dir = out / "scenes"
    video_dir = out / "video"
    final_dir = out / "final"
    out.mkdir(parents=True, exist_ok=True)

    # Phase 0
    emit_event(job_id, "phase", {"phase": "phase0", "status": "running",
                                 "title": "Phase 0 — Style", "detail": "Building deterministic visual contract..."})
    style = build_style_manifest()
    write_style_artifacts(style, out)
    emit_event(job_id, "phase", {"phase": "phase0", "status": "done",
                                 "title": "Phase 0 — Style", "detail": f"palette + 6 layout zones"})

    # Phase 1
    emit_event(job_id, "phase", {"phase": "phase1", "status": "running",
                                 "title": "Phase 1 — Solver", "detail": "Working out the math..."})
    image_hint = f"User uploaded an image: {image_path}" if image_path else None
    solution = await solve(query, image_hint=image_hint)
    emit_event(job_id, "phase", {"phase": "phase1", "status": "done",
                                 "title": "Phase 1 — Solver",
                                 "detail": f"{solution.topic} ({len(solution.steps)} steps)",
                                 "preview": solution.conclusion[:300]})

    # Phase 2
    emit_event(job_id, "phase", {"phase": "phase2", "status": "running",
                                 "title": "Phase 2 — Director", "detail": "Planning scenes..."})
    storyboard = await direct(solution, target_seconds=target_seconds, style=style)
    emit_event(job_id, "phase", {"phase": "phase2", "status": "done",
                                 "title": "Phase 2 — Director",
                                 "detail": f"{len(storyboard.scenes)} scenes / {storyboard.total_target_seconds}s",
                                 "scenes": len(storyboard.scenes)})

    # Phase 3
    emit_event(job_id, "phase", {"phase": "phase3", "status": "running",
                                 "title": "Phase 3 — Narrator + TTS", "detail": "Synthesizing voiceover..."})
    scene_audios = await orchestrator._phase3_fanout(storyboard, audio_dir)
    emit_event(job_id, "phase", {"phase": "phase3", "status": "done",
                                 "title": "Phase 3 — Narrator + TTS",
                                 "detail": f"{len(scene_audios)} MP3s, {sum(a.duration_s for a in scene_audios):.1f}s total"})

    # Phase 4
    emit_event(job_id, "phase", {"phase": "phase4", "status": "running",
                                 "title": "Phase 4 — Animator", "detail": "Generating Manim code (AST-gated)..."})
    scene_codes = await orchestrator._phase4_fanout(storyboard, scene_audios, style, scenes_dir)
    emit_event(job_id, "phase", {"phase": "phase4", "status": "done",
                                 "title": "Phase 4 — Animator",
                                 "detail": f"{len(scene_codes)} scene .py files validated"})

    # Phase 5
    emit_event(job_id, "phase", {"phase": "phase5", "status": "running",
                                 "title": "Phase 5 — Render + Healer", "detail": "Rendering scenes (max 2 in parallel)..."})
    scene_videos = await orchestrator._phase5_fanout(scene_codes, scene_audios, style, video_dir)
    healer_used = sum(1 for v in scene_videos if v.used_healer)
    emit_event(job_id, "phase", {"phase": "phase5", "status": "done",
                                 "title": "Phase 5 — Render + Healer",
                                 "detail": f"{len(scene_videos)} MP4s rendered (healer used on {healer_used})"})

    # Phase 6
    emit_event(job_id, "phase", {"phase": "phase6", "status": "running",
                                 "title": "Phase 6 — Assembler", "detail": "Normalize + concat..."})
    final = await assemble(
        scene_videos=scene_videos,
        scene_audios=scene_audios,
        final_dir=final_dir,
    )
    emit_event(job_id, "phase", {"phase": "phase6", "status": "done",
                                 "title": "Phase 6 — Assembler",
                                 "detail": f"{final.total_duration_s:.2f}s (drift {final.total_drift_ms:+d}ms)"})

    return final


def run_pipeline_job_v2(job_id: str, query: str, image_path: str | None = None,
                        target_seconds: int = 60):
    """Background thread runner for AEVE 2.0 jobs."""
    from models.llm_client import logger as pipeline_logger

    sse_handler = patched_logger_emit(job_id)
    sse_handler.setLevel("INFO")
    from logging import Formatter
    sse_handler.setFormatter(Formatter("%(message)s"))
    pipeline_logger.addHandler(sse_handler)

    try:
        final = asyncio.run(
            _run_aeve2_with_events(
                job_id, query, image_path=image_path, target_seconds=target_seconds
            )
        )
        emit_event(job_id, "complete", {
            "video_path": f"/output/{os.path.basename(str(final.mp4_path))}",
            "absolute_path": str(final.mp4_path),
            "duration_s": final.total_duration_s,
            "drift_ms": final.total_drift_ms,
        })
    except Exception as e:
        emit_event(job_id, "error", {"message": str(e), "type": type(e).__name__})
    finally:
        pipeline_logger.removeHandler(sse_handler)
        emit_event(job_id, "end", {})


# ---------------------------------------------------------------------------
# Legacy AEVE 1.0 worker — preserved for backward compat (mode=legacy).
# ---------------------------------------------------------------------------


def run_pipeline_job(job_id: str, query: str, image_path: str = None, quality: str = "low"):
    """Run the AEVE 1.0 pipeline in a background thread, pushing events to SSE."""
    from models.llm_client import logger as pipeline_logger

    # Add SSE handler to pipeline logger
    sse_handler = patched_logger_emit(job_id)
    sse_handler.setLevel("INFO")
    from logging import Formatter
    sse_handler.setFormatter(Formatter("%(message)s"))
    pipeline_logger.addHandler(sse_handler)

    try:
        # Set quality
        quality_map = {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}
        config.MANIM_QUALITY = quality_map.get(quality, "-ql")

        # ── Phase I ─────────────────────────────────────────
        emit_event(job_id, "phase", {"phase": "phase1", "status": "running", "title": "Phase I: Knowledge Distillation", "detail": "M1 solving + M2 verifying..."})
        from pipeline.phase1_knowledge import run_phase1
        script_1 = run_phase1(query, image_path)

        # Save Script 1
        script1_path = os.path.join(config.OUTPUT_DIR, "script_1_deep_solution.md")
        with open(script1_path, "w", encoding="utf-8") as f:
            f.write(script_1)

        emit_event(job_id, "phase", {"phase": "phase1", "status": "done", "title": "Phase I: Knowledge Distillation", "detail": f"Solution ready ({len(script_1)} chars)", "preview": script_1[:500]})

        # ── Phase II ────────────────────────────────────────
        emit_event(job_id, "phase", {"phase": "phase2", "status": "running", "title": "Phase II: Consensus Committee", "detail": "M3-M6 building scene manifest..."})
        from pipeline.phase2_committee import run_phase2
        scene_manifest = run_phase2(script_1)

        manifest_path = os.path.join(config.OUTPUT_DIR, "scene_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(scene_manifest, f, indent=2, ensure_ascii=False)

        emit_event(job_id, "phase", {"phase": "phase2", "status": "done", "title": "Phase II: Consensus Committee", "detail": f"{len(scene_manifest)} scenes created", "scenes": len(scene_manifest)})

        # ── Phase III ───────────────────────────────────────
        emit_event(job_id, "phase", {"phase": "phase3", "status": "running", "title": "Phase III: Distribution", "detail": "Generating audio + code per scene..."})

        from pipeline.phase3_distributor import run_phase3
        phase3_results = run_phase3(scene_manifest, script_1)

        success_count = sum(1 for r in phase3_results if r["status"] == "success")
        emit_event(job_id, "phase", {"phase": "phase3", "status": "done", "title": "Phase III: Distribution", "detail": f"{success_count}/{len(phase3_results)} scenes generated"})

        # ── Assembly ────────────────────────────────────────
        emit_event(job_id, "phase", {"phase": "assembly", "status": "running", "title": "Assembly", "detail": "Rendering Manim + merging audio..."})

        from renderer.assembler import assemble_final_video
        final_video = assemble_final_video(phase3_results)

        emit_event(job_id, "phase", {"phase": "assembly", "status": "done", "title": "Assembly", "detail": "Final video ready!"})

        # ── Complete ────────────────────────────────────────
        # Copy to static for serving
        relative_video = os.path.relpath(final_video, config.PROJECT_ROOT)
        emit_event(job_id, "complete", {"video_path": f"/output/{os.path.basename(final_video)}", "absolute_path": final_video})

    except Exception as e:
        emit_event(job_id, "error", {"message": str(e), "type": type(e).__name__})
    finally:
        pipeline_logger.removeHandler(sse_handler)
        # Signal end of stream
        emit_event(job_id, "end", {})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_pipeline():
    """Start a new pipeline job.

    Form fields:
        query           — the math topic
        image           — optional image upload
        mode            — "v2" (default, AEVE 2.0) or "legacy" (AEVE 1.0)
        target_seconds  — total runtime target for AEVE 2.0 (default 60)
        quality         — Manim quality flag for AEVE 1.0 (default "low")
    """
    job_id = str(uuid.uuid4())[:8]
    event_queues[job_id] = queue.Queue()

    query = request.form.get("query", "")
    mode = (request.form.get("mode") or "v2").lower()
    image_path = None

    # Handle image upload
    if "image" in request.files:
        file = request.files["image"]
        if file.filename:
            ext = os.path.splitext(file.filename)[1]
            image_path = os.path.join(UPLOAD_DIR, f"input_{job_id}{ext}")
            file.save(image_path)
            if not query:
                query = "Analyze and solve the mathematical problem in the attached image."

    if not query:
        return jsonify({"error": "Please provide a query or upload an image"}), 400

    if mode == "legacy":
        quality = request.form.get("quality", "low")
        thread = threading.Thread(
            target=run_pipeline_job,
            args=(job_id, query, image_path, quality),
            daemon=True,
        )
    else:
        try:
            target_seconds = int(request.form.get("target_seconds", "60"))
        except ValueError:
            target_seconds = 60
        target_seconds = max(20, min(180, target_seconds))
        thread = threading.Thread(
            target=run_pipeline_job_v2,
            args=(job_id, query, image_path, target_seconds),
            daemon=True,
        )
    thread.start()

    return jsonify({"job_id": job_id, "mode": mode})


@app.route("/events/<job_id>")
def events(job_id):
    """Server-Sent Events endpoint for real-time progress."""
    def generate():
        if job_id not in event_queues:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'Job not found'}})}\n\n"
            return

        q = event_queues[job_id]
        while True:
            try:
                event = q.get(timeout=60)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("end", "error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat', 'data': {}})}\n\n"

        # Cleanup
        if job_id in event_queues:
            del event_queues[job_id]

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/output/<path:filename>")
def serve_output(filename):
    """Serve output files. Both legacy (`final_video.mp4`) and AEVE 2.0
    (`final.mp4`) live in `config.FINAL_DIR`."""
    return send_from_directory(config.FINAL_DIR, filename)


if __name__ == "__main__":
    print("\nAEVE Web Interface starting at http://localhost:5000")
    print("Default mode: AEVE 2.0. Pass mode=legacy in form data to use AEVE 1.0.\n")
    app.run(debug=False, port=5000, threaded=True)
