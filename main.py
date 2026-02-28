"""
AEVE — Autonomous Educational Video Engine

Main CLI entrypoint. Runs the full pipeline:
  Phase I:  Knowledge Distillation (M1 + M2)
  Phase II: Consensus Committee (M3-M6)
  Phase III: Distribution (Audio + Code streams)
  Assembly: Render + Merge + Concatenate → Final Video
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from models.llm_client import logger, LLMError, OutputValidationError


def print_banner():
    """Print the AEVE startup banner."""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     ██████╗ ███████╗██╗   ██╗███████╗                        ║
║    ██╔═══██╗██╔════╝██║   ██║██╔════╝                        ║
║    ███████║ █████╗  ██║   ██║█████╗                          ║
║    ██╔══██║ ██╔══╝  ╚██╗ ██╔╝██╔══╝                          ║
║    ██║  ██║ ███████╗ ╚████╔╝ ███████╗                        ║
║    ╚═╝  ╚═╝ ╚══════╝  ╚═══╝  ╚══════╝                        ║
║                                                              ║
║    Autonomous Educational Video Engine                       ║
║    ─────────────────────────────────                         ║
║    Transform any math topic into an animated video           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def check_api_keys():
    """Verify API keys are configured."""
    issues = []
    if config.GROQ_API_KEY == "YOUR_GROQ_API_KEY_HERE":
        issues.append("GROQ_API_KEY not set (get it from console.groq.com)")
    if config.OPENROUTER_API_KEY == "YOUR_OPENROUTER_API_KEY_HERE":
        issues.append("OPENROUTER_API_KEY not set (get it from openrouter.ai/keys)")
    if config.GOOGLE_API_KEY == "YOUR_GOOGLE_API_KEY_HERE":
        issues.append("GOOGLE_API_KEY not set (get it from aistudio.google.com/apikey)")

    if issues:
        print("\n⚠ API KEY CONFIGURATION REQUIRED:")
        print("─" * 40)
        for issue in issues:
            print(f"  ✗ {issue}")
        print()
        print("Set them as environment variables or edit config.py directly.")
        print("Example:")
        print('  set GROQ_API_KEY=gsk_your_key_here')
        print('  set OPENROUTER_API_KEY=sk-or-your_key_here')
        print('  set GOOGLE_API_KEY=AIza_your_key_here')
        print()
        return False
    return True


def run_pipeline(query: str, image_path: str = None, quality: str = None):
    """
    Run the full AEVE pipeline.

    Args:
        query:      The math topic or question
        image_path: Optional path to an image (for OCR input)
        quality:    Manim quality: "low", "medium", "high", "4k"
    """
    start_time = time.time()

    # Set quality
    if quality:
        quality_map = {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}
        config.MANIM_QUALITY = quality_map.get(quality, "-ql")

    logger.info(f"Topic:   {query[:100]}{'...' if len(query) > 100 else ''}")
    logger.info(f"Image:   {image_path or 'None'}")
    logger.info(f"Quality: {config.MANIM_QUALITY}")
    logger.info(f"Output:  {config.OUTPUT_DIR}")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")

    # ═══════════════════════════════════════════════════════════
    # PHASE I: Knowledge Distillation
    # ═══════════════════════════════════════════════════════════
    from pipeline.phase1_knowledge import run_phase1
    script_1 = run_phase1(query, image_path)

    # Save Script 1 for reference
    script1_path = os.path.join(config.OUTPUT_DIR, "script_1_deep_solution.md")
    with open(script1_path, "w", encoding="utf-8") as f:
        f.write(script_1)
    logger.info(f"Script 1 saved → {script1_path}")

    # ═══════════════════════════════════════════════════════════
    # PHASE II: Consensus Committee
    # ═══════════════════════════════════════════════════════════
    from pipeline.phase2_committee import run_phase2
    scene_manifest = run_phase2(script_1)

    # Save Scene Manifest for reference
    manifest_path = os.path.join(config.OUTPUT_DIR, "scene_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(scene_manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"Scene manifest saved → {manifest_path}")

    # ═══════════════════════════════════════════════════════════
    # PHASE III: Distribution (Audio + Code generation)
    # ═══════════════════════════════════════════════════════════
    from pipeline.phase3_distributor import run_phase3
    phase3_results = run_phase3(scene_manifest, script_1)

    # Save Phase 3 results
    results_path = os.path.join(config.OUTPUT_DIR, "phase3_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        # Make results JSON-serializable
        serializable = []
        for r in phase3_results:
            s = {**r}
            if s.get("audio"):
                s["audio"] = {**s["audio"]}
            serializable.append(s)
        json.dump(serializable, f, indent=2, default=str)

    # ═══════════════════════════════════════════════════════════
    # ASSEMBLY: Render + Merge + Final Video
    # ═══════════════════════════════════════════════════════════
    from renderer.assembler import assemble_final_video
    final_video = assemble_final_video(phase3_results)

    # ═══════════════════════════════════════════════════════════
    # DONE
    # ═══════════════════════════════════════════════════════════
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║                  ✓ PIPELINE COMPLETE                    ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  Final Video:  {final_video:<40} ║")
    print(f"║  Time Elapsed: {minutes}m {seconds}s{' ' * 35}║")
    print(f"║  Scenes:       {sum(1 for r in phase3_results if r['status'] == 'success')}/{len(phase3_results)} successful{' ' * 25}║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  All artifacts saved in: output/                       ║")
    print("║  • script_1_deep_solution.md  (Math solution)          ║")
    print("║  • scene_manifest.json        (Scene breakdown)        ║")
    print("║  • scenes/                    (Manim Python files)     ║")
    print("║  • audio/                     (MP3 narration)          ║")
    print("║  • logs/pipeline.log          (Full debug log)         ║")
    print("╚══════════════════════════════════════════════════════════╝")

    return final_video


def main():
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="AEVE — Autonomous Educational Video Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "Prove the Pythagorean theorem"
  python main.py "What is the quadratic formula? Derive it."
  python main.py --image problem.png
  python main.py --quality high "Explain the chain rule in calculus"
        """,
    )

    parser.add_argument(
        "query",
        nargs="?",
        help="The math topic or question to explain",
    )
    parser.add_argument(
        "--image", "-i",
        help="Path to an image file (e.g., a photo of a math problem)",
    )
    parser.add_argument(
        "--quality", "-q",
        choices=["low", "medium", "high", "4k"],
        default="low",
        help="Video quality (default: low for fast development)",
    )

    args = parser.parse_args()

    # Validate input
    if not args.query and not args.image:
        parser.error("Please provide a query or --image path")

    query = args.query or "Analyze and solve the mathematical problem in the attached image."

    print_banner()

    # Check API keys
    if not check_api_keys():
        sys.exit(1)

    try:
        run_pipeline(query, args.image, args.quality)
    except LLMError as e:
        logger.error(f"\n{e}")
        print("\n✗ Pipeline failed due to an LLM error. Check the log above for details.")
        print(f"  Full debug log: {os.path.join(config.LOG_DIR, 'pipeline.log')}")
        sys.exit(1)
    except OutputValidationError as e:
        logger.error(f"\n{e}")
        print("\n✗ Pipeline failed: a model returned unexpected output.")
        print(f"  Full debug log: {os.path.join(config.LOG_DIR, 'pipeline.log')}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠ Pipeline interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(f"\n✗ Unexpected error: {e}")
        print(f"  Full debug log: {os.path.join(config.LOG_DIR, 'pipeline.log')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
