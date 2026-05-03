"""
AEVE — Autonomous Educational Video Engine — CLI entrypoint.

By default this runs the AEVE 2.0 pipeline (5 agents, 6 phases, Pydantic-
gated, 50 ms drift budget). Pass `--legacy` to fall back to the AEVE 1.0
10-agent pipeline during the side-by-side period.

Examples:
    python main.py "Prove the Pythagorean theorem"
    python main.py "Derive the quadratic formula" --target-seconds 90
    python main.py --image problem.png "Explain this"
    python main.py --legacy "Old-pipeline path" --quality high
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path so the package layout works under both
# `python main.py …` and `python -m main`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from models.llm_client import LLMError, OutputValidationError, logger


# ---------------------------------------------------------------------------
# Banner + key check
# ---------------------------------------------------------------------------


def print_banner() -> None:
    banner = """
+--------------------------------------------------------------+
|     AEVE 2.0 — Autonomous Educational Video Engine           |
|     ----------------------------------------------           |
|     Prompt -> Solver -> Director -> Narrator+TTS             |
|              -> Animator -> Render+Healer -> Assembler       |
+--------------------------------------------------------------+
"""
    print(banner)


def check_api_keys() -> bool:
    issues = []
    if not config.GROQ_API_KEY or config.GROQ_API_KEY.startswith("YOUR_"):
        issues.append("GROQ_API_KEY not set (console.groq.com)")
    if not config.OPENROUTER_API_KEY or config.OPENROUTER_API_KEY.startswith("YOUR_"):
        issues.append("OPENROUTER_API_KEY not set (openrouter.ai/keys)")
    if issues:
        print("\nAPI KEY CONFIGURATION REQUIRED:")
        print("-" * 40)
        for issue in issues:
            print(f"  - {issue}")
        print("\nEdit config.py or set environment variables before running.")
        return False
    return True


# ---------------------------------------------------------------------------
# AEVE 2.0 path
# ---------------------------------------------------------------------------


async def _run_aeve2(
    query: str,
    *,
    image_path: str | None,
    target_seconds: int,
    output_dir: Path | None,
) -> None:
    """Run the AEVE 2.0 pipeline and print a final report."""
    from pipeline.orchestrator import run_pipeline

    image_hint: str | None = None
    if image_path:
        image_hint = f"User uploaded an image: {image_path}"

    start = time.time()
    result = await run_pipeline(
        query,
        target_seconds=target_seconds,
        image_hint=image_hint,
        output_dir=output_dir,
    )
    elapsed = time.time() - start

    audio_total = sum(a.duration_s for a in result.scene_audios)
    final = result.final_video

    print()
    print("=" * 60)
    print("  AEVE 2.0 — pipeline complete")
    print("=" * 60)
    print(f"  Topic:       {result.solution.topic}")
    print(f"  Difficulty:  {result.solution.difficulty}")
    print(f"  Scenes:      {len(result.storyboard.scenes)}")
    print(f"  Audio total: {audio_total:.2f}s")
    print(f"  Final mp4:   {final.mp4_path}")
    print(
        f"  Final dur:   {final.total_duration_s:.2f}s "
        f"(drift {final.total_drift_ms:+d}ms)"
    )
    print(f"  Wall clock:  {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Legacy AEVE 1.0 path (preserved for the side-by-side period)
# ---------------------------------------------------------------------------


def _run_legacy(query: str, image_path: str | None, quality: str | None) -> str:
    """Legacy AEVE 1.0 flow — Phase I/II/III + assemble_final_video()."""
    start_time = time.time()

    if quality:
        quality_map = {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}
        config.MANIM_QUALITY = quality_map.get(quality, "-ql")

    logger.info(f"[legacy] Topic:   {query[:100]}{'...' if len(query) > 100 else ''}")
    logger.info(f"[legacy] Image:   {image_path or 'None'}")
    logger.info(f"[legacy] Quality: {config.MANIM_QUALITY}")
    logger.info(f"[legacy] Output:  {config.OUTPUT_DIR}")
    logger.info(f"[legacy] Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    from pipeline.phase1_knowledge import run_phase1

    script_1 = run_phase1(query, image_path)
    script1_path = os.path.join(config.OUTPUT_DIR, "script_1_deep_solution.md")
    with open(script1_path, "w", encoding="utf-8") as f:
        f.write(script_1)
    logger.info(f"[legacy] Script 1 saved -> {script1_path}")

    from pipeline.phase2_committee import run_phase2

    scene_manifest = run_phase2(script_1)
    manifest_path = os.path.join(config.OUTPUT_DIR, "scene_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(scene_manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"[legacy] Scene manifest saved -> {manifest_path}")

    from pipeline.phase3_distributor import run_phase3

    phase3_results = run_phase3(scene_manifest, script_1)
    results_path = os.path.join(config.OUTPUT_DIR, "phase3_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        serializable = []
        for r in phase3_results:
            s = {**r}
            if s.get("audio"):
                s["audio"] = {**s["audio"]}
            serializable.append(s)
        json.dump(serializable, f, indent=2, default=str)

    from renderer.assembler import assemble_final_video

    final_video = assemble_final_video(phase3_results)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print("  AEVE 1.0 (legacy) — pipeline complete")
    print("=" * 60)
    print(f"  Final Video: {final_video}")
    print(f"  Wall clock:  {int(elapsed // 60)}m {int(elapsed % 60)}s")
    print(
        f"  Scenes:      "
        f"{sum(1 for r in phase3_results if r['status'] == 'success')}/{len(phase3_results)}"
    )
    print("=" * 60)
    return final_video


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AEVE — Autonomous Educational Video Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "Prove the Pythagorean theorem"
  python main.py "Derive the quadratic formula" --target-seconds 90
  python main.py --image problem.png "Explain this image"
  python main.py --legacy "Old pipeline" --quality high
        """,
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="The math topic or question to explain",
    )
    parser.add_argument(
        "--image", "-i",
        help="Path to an image file (e.g. a photo of a problem)",
    )
    parser.add_argument(
        "--target-seconds", "-t",
        type=int,
        default=60,
        help="Target total runtime in seconds (AEVE 2.0). Clamped to [20, 180].",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: config.OUTPUT_DIR).",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use the AEVE 1.0 10-agent pipeline (preserved for backward compat).",
    )
    parser.add_argument(
        "--quality", "-q",
        choices=["low", "medium", "high", "4k"],
        default=None,
        help="Manim quality flag. AEVE 1.0 only — AEVE 2.0 hardcodes 1080p30.",
    )
    args = parser.parse_args()

    if not args.query and not args.image:
        parser.error("Please provide a query or --image path")
    query = args.query or "Analyze and solve the math problem in the attached image."

    print_banner()
    if not check_api_keys():
        sys.exit(1)

    try:
        if args.legacy:
            if args.quality is None:
                args.quality = "low"
            _run_legacy(query, args.image, args.quality)
        else:
            if args.quality is not None:
                print(
                    "[note] --quality is ignored in AEVE 2.0 mode "
                    "(hardcoded 1080p30). Pass --legacy to use it."
                )
            asyncio.run(
                _run_aeve2(
                    query,
                    image_path=args.image,
                    target_seconds=args.target_seconds,
                    output_dir=args.output_dir,
                )
            )
    except LLMError as exc:
        logger.error(f"\n{exc}")
        print("\nPipeline failed due to an LLM error. See the log for details.")
        print(f"  Full debug log: {os.path.join(config.LOG_DIR, 'pipeline.log')}")
        sys.exit(1)
    except OutputValidationError as exc:
        logger.error(f"\n{exc}")
        print("\nPipeline failed: a model returned unexpected output.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        logger.exception(f"Unexpected error: {exc}")
        print(f"\nUnexpected error: {exc}")
        print(f"  Full debug log: {os.path.join(config.LOG_DIR, 'pipeline.log')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
