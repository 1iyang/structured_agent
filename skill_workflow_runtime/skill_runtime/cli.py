from __future__ import annotations

import argparse
import os


def add_local_llm_backend_args(
    parser: argparse.ArgumentParser,
    *,
    max_num_seqs_help: str = "Optional max_num_seqs override for local-vllm mode.",
    enforce_eager_help: str = "Force eager execution for local-vllm mode.",
    allow_cuda_graphs_help: str = "Allow the compiled / CUDA-graph path for local-vllm mode.",
) -> None:
    parser.add_argument(
        "--llm-mode",
        choices=["deterministic", "local-vllm"],
        help="Optional LLM backend mode override.",
    )
    parser.add_argument(
        "--llm-model",
        help="Optional local model path or model name override for local-vllm mode.",
    )
    parser.add_argument(
        "--max-model-len",
        type=int,
        help="Optional max model length override for local-vllm mode.",
    )
    parser.add_argument(
        "--tensor-parallel-size",
        type=int,
        help="Optional tensor parallel size override for local-vllm mode.",
    )
    parser.add_argument(
        "--gpu-memory-utilization",
        type=float,
        help="Optional GPU memory utilization override for local-vllm mode.",
    )
    parser.add_argument(
        "--cuda-visible-devices",
        help="Optional CUDA_VISIBLE_DEVICES override, for example `1` to run on GPU1.",
    )
    parser.add_argument(
        "--max-num-seqs",
        type=int,
        help=max_num_seqs_help,
    )
    parser.add_argument(
        "--max-num-batched-tokens",
        type=int,
        help="Optional max_num_batched_tokens override for local-vllm mode.",
    )
    eager_group = parser.add_mutually_exclusive_group()
    eager_group.add_argument(
        "--enforce-eager",
        dest="enforce_eager",
        action="store_true",
        help=enforce_eager_help,
    )
    eager_group.add_argument(
        "--allow-cuda-graphs",
        dest="enforce_eager",
        action="store_false",
        help=allow_cuda_graphs_help,
    )
    parser.set_defaults(enforce_eager=None)


def apply_local_llm_backend_overrides(args: argparse.Namespace) -> None:
    if getattr(args, "llm_mode", None):
        os.environ["SKILL_RUNTIME_LLM_MODE"] = args.llm_mode
    if getattr(args, "llm_model", None):
        os.environ["SKILL_RUNTIME_LLM_MODEL"] = args.llm_model
    if getattr(args, "max_model_len", None) is not None:
        os.environ["SKILL_RUNTIME_MAX_MODEL_LEN"] = str(args.max_model_len)
    if getattr(args, "tensor_parallel_size", None) is not None:
        os.environ["SKILL_RUNTIME_TENSOR_PARALLEL_SIZE"] = str(args.tensor_parallel_size)
    if getattr(args, "gpu_memory_utilization", None) is not None:
        os.environ["SKILL_RUNTIME_GPU_MEMORY_UTILIZATION"] = str(args.gpu_memory_utilization)
    if getattr(args, "cuda_visible_devices", None):
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices
    if getattr(args, "max_num_seqs", None) is not None:
        os.environ["SKILL_RUNTIME_MAX_NUM_SEQS"] = str(args.max_num_seqs)
    if getattr(args, "max_num_batched_tokens", None) is not None:
        os.environ["SKILL_RUNTIME_MAX_NUM_BATCHED_TOKENS"] = str(args.max_num_batched_tokens)
    if getattr(args, "enforce_eager", None) is not None:
        os.environ["SKILL_RUNTIME_ENFORCE_EAGER"] = "true" if args.enforce_eager else "false"
