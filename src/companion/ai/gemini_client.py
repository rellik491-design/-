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
        "gemini-3.8-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-pro-latest",
    ]

    VERTEX_CANDIDATE_MODELS = [
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-2.5-pro",
    ]

    _active_key_index: int = 0

    @classmethod
    def get_api_key(cls, explicit_key: Optional[str] = None) -> Optional[str]:
        keys = cls.get_api_keys(explicit_key)
        return keys[0] if keys else None

    @classmethod
    def get_api_keys(cls, explicit_key: Optional[str] = None) -> List[str]:
        """
        Returns an ordered list of configured API keys for automatic failover and rotation.
        """
        keys: List[str] = []
        if explicit_key and explicit_key.strip():
            keys.append(explicit_key.strip())

        # Check settings.GEMINI_API_KEYS
        env_keys = getattr(settings, "GEMINI_API_KEYS", None) or os.environ.get("GEMINI_API_KEYS", "")
        if isinstance(env_keys, str) and env_keys.strip():
            for k in env_keys.replace(";", ",").replace("\n", ",").split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        elif isinstance(env_keys, (list, tuple)):
            for k in env_keys:
                k = str(k).strip()
                if k and k not in keys:
                    keys.append(k)

        # Fallback to single GEMINI_API_KEY
        single = getattr(settings, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY", "")
        if single and str(single).strip() and str(single).strip() not in keys:
            keys.append(str(single).strip())

        return keys

    @classmethod
    def rotate_to_next_key(cls, failed_key: Optional[str] = None) -> Optional[str]:
        """Advances active key pointer to the next key in the pool."""
        keys = cls.get_api_keys()
        if not keys:
            return None
        if failed_key and failed_key in keys:
            curr = keys.index(failed_key)
            cls._active_key_index = (curr + 1) % len(keys)
        else:
            cls._active_key_index = (cls._active_key_index + 1) % len(keys)
        return keys[cls._active_key_index]

    @classmethod
    def get_candidate_models(cls, primary_model: Optional[str] = None) -> List[str]:
        use_vertex = getattr(settings, "USE_VERTEX_AI", False)
        base_models = cls.VERTEX_CANDIDATE_MODELS if use_vertex else cls.DEFAULT_FALLBACK_MODELS
        first = primary_model or settings.GEMINI_MODEL or (base_models[0])
        models = [first]
        for m in base_models:
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
        use_vertex = getattr(settings, "USE_VERTEX_AI", False)
        candidate_keys = cls.get_api_keys(api_key)
        if not use_vertex and not candidate_keys:
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

        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        gen_config = types.GenerateContentConfig(**config_kwargs)

        start_ts = time.perf_counter()
        last_error: Optional[Exception] = None

        # 1. Primary Vertex AI attempt (if explicitly enabled)
        if use_vertex:
            client = genai.Client(
                vertexai=True,
                project=getattr(settings, "VERTEX_PROJECT", "gen-lang-client-0915483038"),
                location=getattr(settings, "VERTEX_LOCATION", "us-central1"),
            )
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
                                "vertex_generate_success model=%s attempt=%d duration_ms=%s",
                                model_name,
                                attempt + 1,
                                duration_ms,
                            )
                            return resp.text.strip()
                    except asyncio.TimeoutError as e:
                        last_error = e
                    except Exception as e:
                        last_error = e
                        err_str = str(e)
                        if "404" in err_str or "NOT_FOUND" in err_str:
                            break
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            if attempt < retries:
                                await asyncio.sleep(1.0 * (2 ** attempt))
                                continue
                            break
                    if attempt < retries:
                        await asyncio.sleep(0.4 * (2 ** attempt))

            if not candidate_keys:
                logger.error("vertex_failed_and_no_fallback_keys last_error=%s", last_error)
                return None
            logger.warning("[VERTEX_AI] Vertex AI failed (%s). Falling back to Gemini API Key Rotation Pool...", last_error)

        # 2. Key Rotation Pool (Tries keys in order, rotating upon error)
        start_idx = cls._active_key_index % len(candidate_keys)
        ordered_keys = candidate_keys[start_idx:] + candidate_keys[:start_idx]

        for current_key in ordered_keys:
            masked_key = (
                f"{current_key[:6]}...{current_key[-4:]}"
                if len(current_key) > 12
                else "***"
            )
            client = genai.Client(api_key=current_key)
            key_exhausted_or_invalid = False

            for model_name in candidate_models:
                if key_exhausted_or_invalid:
                    break

                for attempt in range(retries + 1):
                    attempt_start = time.perf_counter()
                    try:
                        resp = await asyncio.wait_for(
                            cls._invoke_sdk(client, model_name, contents, gen_config),
                            timeout=timeout,
                        )
                        duration_ms = round((time.perf_counter() - attempt_start) * 1000, 1)
                        if resp and getattr(resp, "text", None):
                            # Retain active working key for subsequent requests
                            cls._active_key_index = candidate_keys.index(current_key)
                            logger.info(
                                "gemini_generate_success key=%s model=%s attempt=%d duration_ms=%s",
                                masked_key,
                                model_name,
                                attempt + 1,
                                duration_ms,
                            )
                            return resp.text.strip()
                    except asyncio.TimeoutError as e:
                        last_error = e
                        logger.warning(
                            "gemini_generate_timeout key=%s model=%s attempt=%d timeout_s=%s",
                            masked_key,
                            model_name,
                            attempt + 1,
                            timeout,
                        )
                    except Exception as e:
                        last_error = e
                        err_str = str(e)
                        logger.warning(
                            "gemini_generate_error key=%s model=%s attempt=%d error=%s",
                            masked_key,
                            model_name,
                            attempt + 1,
                            err_str[:200],
                        )

                        # Model not available or retired: switch candidate model on same key
                        if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                            break

                        # Model temporarily unavailable: switch candidate model
                        if "503" in err_str or "UNAVAILABLE" in err_str:
                            logger.warning("[GEMINI] Model %s 503 unavailable. Falling back to next model...", model_name)
                            break

                        # Key-level errors: Auth/disabled (401), Rate limit (429), payment/prepay (402), forbidden (403), daily quota
                        if any(sig in err_str.lower() for sig in [
                            "401", "unauthenticated", "account_state_invalid", "deleted or disabled",
                            "429", "resource_exhausted", "402", "prepayment", "depleted",
                            "403", "permission_denied", "quota", "per day", "api key not valid"
                        ]):
                            logger.warning(
                                "[GEMINI_ROTATION] Key %s encountered error (%s). Rotating to next key in pool...",
                                masked_key,
                                err_str[:120],
                            )
                            key_exhausted_or_invalid = True
                            cls.rotate_to_next_key(current_key)
                            break

                    if attempt < retries:
                        await asyncio.sleep(0.4 * (2 ** attempt))

        total_ms = round((time.perf_counter() - start_ts) * 1000, 1)
        logger.error(
            "gemini_all_keys_and_models_failed total_ms=%s last_error=%s",
            total_ms,
            str(last_error)[:200] if last_error else "empty_response",
        )
        return None
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
