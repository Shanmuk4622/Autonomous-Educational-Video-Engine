"""
AEVE — Autonomous Educational Video Engine — CLI entrypoint.

Runs the AEVE 2.0 pipeline (5 agents, 6 phases, Pydantic-gated, 50 ms
drift budget) end-to-end.

Examples:
    python main.py "Prove the Pythagorean theorem"
    python main.py "Derive the quadratic formula" --target-seconds 90
    python main.py --image problem.png "Explain this image"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Add project root to path so the package layout works under both
# `python main.py …` and `python -m main`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from pipeline.llm_clients.errors import LLMError, OutputValidationError

logger = logging.getLogger("AEVE")


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
# AEVE 2.0 pipeline driver
# ---------------------------------------------------------------------------


async def _run(
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
        help="Target total runtime in seconds. Clamped to [20, 180].",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: config.OUTPUT_DIR).",
    )
    args = parser.parse_args()

    if not args.query and not args.image:
        parser.error("Please provide a query or --image path")
    query = args.query or "Analyze and solve the math problem in the attached image."

    print_banner()
    if not check_api_keys():
        sys.exit(1)

    try:
        asyncio.run(
            _run(
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
