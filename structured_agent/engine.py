"""vLLM inference engine for local structured and unstructured generation."""

import json
import time
from typing import List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

try:
    from vllm import LLM, SamplingParams
    from vllm.sampling_params import StructuredOutputsParams
except ImportError:
    LLM = None  # type: ignore[assignment]
    SamplingParams = None  # type: ignore[assignment]
    StructuredOutputsParams = None  # type: ignore[assignment]

from .schemas import StageLog
from .utils import safe_json_loads

T = TypeVar("T", bound=BaseModel)


class VLLMRunner:
    """
    Wrapper around vLLM for inference with structured and text outputs.
    """

    def __init__(
        self,
        model_name: str,
        max_model_len: int = 8192,
        tensor_parallel_size: int = 1,
        gpu_memory_utilization: float = 0.9,
        enable_prefix_caching: bool = True,
    ) -> None:
        """
        Initialize vLLM runner.
        
        Args:
            model_name: Model identifier (e.g., Qwen/Qwen2.5-3B-Instruct)
            max_model_len: Maximum model length
            tensor_parallel_size: Number of GPUs for tensor parallelism
            gpu_memory_utilization: GPU memory utilization rate
            enable_prefix_caching: Enable prefix caching for performance
        """
        if LLM is None or SamplingParams is None or StructuredOutputsParams is None:
            raise RuntimeError(
                "vLLM is not installed. Please install dependencies from requirements.txt "
                "before running the local workflows."
            )
        self.llm = LLM(
            model=model_name,
            max_model_len=max_model_len,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_utilization,
            enable_prefix_caching=enable_prefix_caching,
        )
        self.tokenizer = self.llm.get_tokenizer()

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text using model tokenizer.
        
        Args:
            text: Text to count
            
        Returns:
            Number of tokens
        """
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def _generate_once(self, prompt: str, sampling_params: SamplingParams) -> str:
        """
        Generate response for a single prompt.
        
        Args:
            prompt: Prompt text
            sampling_params: vLLM sampling parameters
            
        Returns:
            Generated text
        """
        outputs = self.llm.generate(prompts=[prompt], sampling_params=sampling_params)
        return outputs[0].outputs[0].text

    def generate_json(
        self,
        prompt: str,
        schema_model: Type[T],
        max_tokens: int = 256,
        temperature: float = 0.0,
        top_p: float = 1.0,
        stage_name: Optional[str] = None,
    ) -> tuple[T, StageLog]:
        """
        Generate JSON-structured output conforming to a Pydantic schema.
        
        Args:
            prompt: Input prompt
            schema_model: Pydantic model to validate output against
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            stage_name: Optional log stage name override
            
        Returns:
            Tuple of (parsed_object, stage_log)
            
        Raises:
            RuntimeError: If parsing fails
        """
        schema = schema_model.model_json_schema()
        prompt_tokens = self.count_tokens(prompt)
        attempt_max_tokens = [max_tokens]
        retry_max_tokens = max(max_tokens + 256, int(max_tokens * 1.75))
        if retry_max_tokens > max_tokens:
            attempt_max_tokens.append(retry_max_tokens)

        total_latency = 0.0
        total_output_chars = 0
        total_output_tokens = 0
        attempts_used = 0
        raw_text = ""
        parsed_ok = False
        last_error: Exception | None = None

        for attempt_tokens in attempt_max_tokens:
            attempts_used += 1
            sop = StructuredOutputsParams(json=schema)
            sampling_params = SamplingParams(
                temperature=temperature,
                top_p=top_p,
                max_tokens=attempt_tokens,
                structured_outputs=sop,
            )

            t0 = time.perf_counter()
            raw_text = self._generate_once(prompt, sampling_params)
            latency = time.perf_counter() - t0
            total_latency += latency
            total_output_chars += len(raw_text)
            total_output_tokens += self.count_tokens(raw_text)

            try:
                payload = safe_json_loads(raw_text)
                parsed = schema_model.model_validate(payload)
                parsed_ok = True
                break
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
        else:
            token_trace = ", ".join(str(value) for value in attempt_max_tokens)
            raise RuntimeError(
                f"Failed to parse structured output for {schema_model.__name__} "
                f"after attempts with max_tokens={token_trace}.\n"
                f"Raw text:\n{raw_text}\n\nError: {last_error}"
            ) from last_error

        log = StageLog(
            stage_name=stage_name or schema_model.__name__,
            prompt_chars=len(prompt) * attempts_used,
            prompt_tokens=prompt_tokens * attempts_used,
            output_chars=total_output_chars,
            output_tokens=total_output_tokens,
            latency_s=total_latency,
            parsed_ok=parsed_ok,
            raw_text=raw_text,
        )
        return parsed, log

    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        top_p: float = 1.0,
        stop: Optional[List[str]] = None,
        stage_name: str = "text_stage",
    ) -> tuple[str, StageLog]:
        """
        Generate free-form text output.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            stop: Stop sequences
            stage_name: Name for logging
            
        Returns:
            Tuple of (generated_text, stage_log)
        """
        sampling_params = SamplingParams(
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
        )

        prompt_tokens = self.count_tokens(prompt)
        t0 = time.perf_counter()
        raw_text = self._generate_once(prompt, sampling_params)
        latency = time.perf_counter() - t0

        log = StageLog(
            stage_name=stage_name,
            prompt_chars=len(prompt),
            prompt_tokens=prompt_tokens,
            output_chars=len(raw_text),
            output_tokens=self.count_tokens(raw_text),
            latency_s=latency,
            parsed_ok=True,
            raw_text=raw_text,
        )
        return raw_text, log
