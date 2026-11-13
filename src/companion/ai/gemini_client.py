import asyncio
import inspect
import logging
import os
import time
from typing import Any, Dict, List, Optional

from companion.config import settings

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Production-grade asynchronous Google Gemini API client.
    Provides:
    - Non-blocking async generation (`client.aio.models.generate_content`)
    - Configurable timeout & exponential backoff retries
    - Automatic model fallback chain
    - Structured telemetry logging (latency, model, retries) without leaking secrets
    """

    DEFAULT_FALLBACK_MODELS = [
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
        "gemini-pro-latest",
    ]

    @classmethod
    def get_api_key(cls, explicit_key: Optional[str] = None) -> Optional[str]:
        return explicit_key or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")

    @classmethod
    def get_candidate_models(cls, primary_model: Optional[str] = None) -> List[str]:
        first = primary_model or settings.GEMINI_MODEL or "gemini-flash-latest"
        models = [first]
        for m in cls.DEFAULT_FALLBACK_MODELS:
            if m not in models:
                models.append(m)
        return models

    @classmethod
    async def generate_content(
        cls,
        contents: Any,
        system_instruction: Optional[str] = None,
        temperature: float = 0.75,
        max_output_tokens: int = 350,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[str]:
        """
        Executes a Gemini generation call with retries, timeout, and model fallback.
        Returns the generated text or None if all attempts fail.
        """
        resolved_key = cls.get_api_key(api_key)
        if not resolved_key:
            return None

        timeout = timeout_seconds or getattr(settings, "GEMINI_TIMEOUT_SECONDS", 25.0)
        retries = max_retries if max_retries is not None else getattr(settings, "GEMINI_MAX_RETRIES", 2)
        candidate_models = cls.get_candidate_models(model)

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            logger.error("google-genai package is not installed")
            return None

        client = genai.Client(api_key=resolved_key)
        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        gen_config = types.GenerateContentConfig(**config_kwargs)

        start_ts = time.perf_counter()
        last_error: Optional[Exception] = None

        for model_name in candidate_models:
            for attempt in range(retries + 1):
                attempt_start = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(
                        cls._invoke_sdk(client, model_name, contents, gen_config),
                        timeout=timeout,
                    )
                    duration_ms = round((time.perf_counter() - attempt_start) * 1000, 1)
                    if resp and getattr(resp, "text", None):
                        logger.info(
                            "gemini_generate_success model=%s attempt=%d duration_ms=%s",
                            model_name,
                            attempt + 1,
                            duration_ms,
                        )
                        return resp.text.strip()
                except asyncio.TimeoutError as e:
                    last_error = e
                    logger.warning(
                        "gemini_generate_timeout model=%s attempt=%d timeout_s=%s",
                        model_name,
                        attempt + 1,
                        timeout,
                    )
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    logger.warning(
                        "gemini_generate_error model=%s attempt=%d error=%s",
                        model_name,
                        attempt + 1,
                        err_str[:200],
                    )
                    # If model not found (404), switch to next candidate model immediately
                    if "404" in err_str or "NOT_FOUND" in err_str:
                        break

                    # If model high demand / temporary overload (503), switch immediately
                    if "503" in err_str or "UNAVAILABLE" in err_str:
                        logger.warning("[GEMINI] Model %s 503 unavailable. Falling back immediately...", model_name)
                        break

                    # If daily quota limit exceeded on this model, switch to next model immediately
                    if "per day" in err_str.lower() or "generaterequestsperday" in err_str.lower():
                        logger.warning("[GEMINI] Daily quota exceeded for %s. Falling back to alternative model...", model_name)
                        break

                    # If temporary rate limit (429 / RPM), backoff and retry this model
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        if attempt < retries:
                            backoff = 1.2 * (2 ** attempt)
                            logger.info("[GEMINI] Rate limited on %s. Retrying in %.1fs...", model_name, backoff)
                            await asyncio.sleep(backoff)
                            continue
                        else:
                            break

                if attempt < retries:
                    await asyncio.sleep(0.4 * (2 ** attempt))

        total_ms = round((time.perf_counter() - start_ts) * 1000, 1)
        logger.error(
            "gemini_all_models_failed total_ms=%s last_error=%s",
            total_ms,
            str(last_error)[:200] if last_error else "empty_response",
        )
        return None

    @classmethod
    async def _invoke_sdk(cls, client: Any, model_name: str, contents: Any, config: Any) -> Any:
        """
        Uses async `client.aio.models.generate_content` by default, with safe fallback
        to thread-offloaded `client.models.generate_content` if mocked in unit tests.
        """
        aio_attr = getattr(client, "aio", None)
        if aio_attr is not None and hasattr(aio_attr, "models"):
            aio_fn = getattr(aio_attr.models, "generate_content", None)
            if aio_fn is not None and inspect.iscoroutinefunction(aio_fn):
                return await aio_fn(model=model_name, contents=contents, config=config)

        # Fallback for sync mocks or older SDK objects: run in worker thread to never block event loop
        return await asyncio.to_thread(
            client.models.generate_content,
            model=model_name,
            contents=contents,
            config=config,
        )
