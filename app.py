"""
AEVE Web Frontend — Interactive web interface with real-time pipeline progress.

Features:
- Text input or image upload
- Real-time step-by-step progress via Server-Sent Events
- Visual pipeline diagram showing current step
- Final video playback
"""

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


def run_pipeline_job(job_id: str, query: str, image_path: str = None, quality: str = "low"):
    """Run the AEVE pipeline in a background thread, pushing events to SSE."""
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
    """Start a new pipeline job."""
    job_id = str(uuid.uuid4())[:8]
    event_queues[job_id] = queue.Queue()

    query = request.form.get("query", "")
    quality = request.form.get("quality", "low")
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

    # Start pipeline in background thread
    thread = threading.Thread(
        target=run_pipeline_job,
        args=(job_id, query, image_path, quality),
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
    """Serve output files (videos, audio, etc.)."""
    return send_from_directory(config.FINAL_DIR, filename)


if __name__ == "__main__":
    print("\n🌐 AEVE Web Interface starting at http://localhost:5000\n")
    app.run(debug=False, port=5000, threaded=True)
