"""Shared helpers for the local agent experiment scripts."""

from __future__ import annotations

import argparse
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from .engine import VLLMRunner

T = TypeVar("T")


def print_progress(message: str) -> None:
    """Emit a flushed progress message for long-running stages."""
    print(f"[progress] {message}", flush=True)


def run_with_heartbeat(
    label: str,
    action: Callable[[], T],
    interval_seconds: float = 5.0,
) -> T:
    """Run an action while periodically printing elapsed time."""
    start = time.monotonic()
    stop_event = threading.Event()

    def heartbeat() -> None:
        while not stop_event.wait(interval_seconds):
            elapsed = time.monotonic() - start
            print_progress(f"{label}... elapsed {elapsed:.0f}s")

    print_progress(f"{label}...")
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        result = action()
    except Exception:
        elapsed = time.monotonic() - start
        print_progress(f"{label} failed after {elapsed:.1f}s")
        raise
    finally:
        stop_event.set()
        thread.join(timeout=0.1)

    elapsed = time.monotonic() - start
    print_progress(f"{label} completed in {elapsed:.1f}s")
    return result


def reset_run_outputs(output_path: Path, artifact_output_dir: str) -> None:
    """Remove stale artifacts for a fresh run."""
    artifact_path = Path(artifact_output_dir)
    if output_path.exists():
        output_path.unlink()
        print_progress(f"Removed previous report `{output_path}`")
    if artifact_path.exists():
        shutil.rmtree(artifact_path)
        print_progress(f"Removed previous artifacts `{artifact_path}`")


def default_artifact_output_dir(output_path: str) -> str:
    """Derive a stable artifact path from the final report path."""
    return f"{Path(output_path).with_suffix('')}_artifacts"


def add_common_runtime_args(parser: argparse.ArgumentParser) -> None:
    """Add shared runtime arguments used by both experiment scripts."""
    parser.add_argument("--model", type=str, required=True, help="Model name or local path")
    parser.add_argument("--output", type=str, required=True, help="Output report path (.md or .json)")
    parser.add_argument(
        "--artifact-output-dir",
        type=str,
        default=None,
        help="Directory for per-case or per-issue artifacts",
    )
    parser.add_argument("--max-model-len", type=int, default=8192, help="Max model context length")
    parser.add_argument("--tensor-parallel-size", type=int, default=1, help="Tensor parallelism")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9, help="GPU memory utilization")
    parser.add_argument("--disable-prefix-caching", action="store_true", help="Disable vLLM prefix caching")


def add_slo_args(parser: argparse.ArgumentParser) -> None:
    """Add shared SLO-policy arguments."""
    parser.add_argument("--slo-seconds", type=float, default=8.0, help="End-to-end SLO budget.")
    parser.add_argument(
        "--retrieval-elapsed-seconds",
        type=float,
        default=0.0,
        help="Time already spent before the routed suffix starts.",
    )
    parser.add_argument(
        "--seconds-per-token",
        type=float,
        default=0.0005,
        help="Estimated additional read cost per token.",
    )
    parser.add_argument(
        "--view-read-overhead-seconds",
        type=float,
        default=0.01,
        help="Fixed per-view read overhead.",
    )
    parser.add_argument(
        "--stage-safety-margin-seconds",
        type=float,
        default=0.2,
        help="Safety margin reserved for each routed stage.",
    )
    parser.add_argument(
        "--optional-selection-score-threshold",
        type=float,
        default=0.0,
        help="Minimum optional read score before a view is included.",
    )


def build_runner(args: argparse.Namespace) -> VLLMRunner:
    """Create the vLLM runner from script arguments."""
    return VLLMRunner(
        model_name=args.model,
        max_model_len=args.max_model_len,
        tensor_parallel_size=args.tensor_parallel_size,
        gpu_memory_utilization=args.gpu_memory_utilization,
        enable_prefix_caching=not args.disable_prefix_caching,
    )
