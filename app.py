"""
AEVE 2.0 Web Frontend — Flask + SSE.

`/start` runs the AEVE 2.0 pipeline (5 agents, 6 phases) in a background
thread, streaming progress as Server-Sent Events to the browser.

SSE events emitted:
    {"type": "phase", "data": {"phase": "phase0".."phase6", "status": "running"|"done", ...}}
    {"type": "log",   "data": {"level": "info|warning|error", "message": ...}}
    {"type": "complete", "data": {"video_path": "...", "duration_s": ..., "drift_ms": ...}}
    {"type": "error",  "data": {"message": ..., "type": ...}}
    {"type": "end",    "data": {}}
"""

import asyncio
import logging
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
    pipeline_logger = logging.getLogger("AEVE")

    sse_handler = patched_logger_emit(job_id)
    sse_handler.setLevel("INFO")
    sse_handler.setFormatter(logging.Formatter("%(message)s"))
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_pipeline():
    """Start a new AEVE 2.0 pipeline job.

    Form fields:
        query           — the math topic (required if no image)
        image           — optional image upload
        target_seconds  — desired total runtime (default 60, clamped to [20, 180])
    """
    job_id = str(uuid.uuid4())[:8]
    event_queues[job_id] = queue.Queue()

    query = request.form.get("query", "")
    image_path = None

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

    return jsonify({"job_id": job_id})


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
    """Serve output files (the assembled `final.mp4` lives in `config.FINAL_DIR`)."""
    return send_from_directory(config.FINAL_DIR, filename)


if __name__ == "__main__":
    print("\nAEVE 2.0 Web Interface starting at http://localhost:5000\n")
    app.run(debug=False, port=5000, threaded=True)
