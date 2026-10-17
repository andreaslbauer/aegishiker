"""Local LLM access through Ollama with host safety rails and Pydantic JSON-schema retry."""
import json
import logging
from typing import Any, Dict, List, Optional, Type, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from . import llm_log, settings
from .ollama_check import resolve_host

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class RemoteHostRefused(RuntimeError):
    pass


def is_local_host(host: str) -> bool:
    return (urlparse(resolve_host(host)).hostname or "").lower() in LOCAL_HOSTS


class LLMService:
    def __init__(
        self,
        host: Optional[Any] = None,
        interview_model: Optional[str] = None,
        chat_model: Optional[str] = None,
        client: Any = None,
    ):
        cfg = settings.load()
        if hasattr(host, "get") and not isinstance(host, str):
            host_str = str(host.get("ollama_host", "") or "")
            interview_model = interview_model or str(host.get("interview_model", cfg["interview_model"]))
            chat_model = chat_model or str(host.get("ollama_model", cfg["chat_model"]))
        else:
            host_str = host if isinstance(host, str) else cfg["ollama_host"]
        self.host = resolve_host(host_str)
        self.interview_model = interview_model or cfg["interview_model"]
        self.chat_model = chat_model or cfg["chat_model"]
        if not is_local_host(self.host) and not cfg.get("allow_remote_ollama"):
            raise RemoteHostRefused(
                f"Ollama host {self.host} is not on this computer. Allow remote hosts in "
                "Settings if you really want your hike plans sent there."
            )
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self.host)
        return self._client

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        format: Any = None,
        temperature: Optional[float] = None,
        kind: str = "chat",
        num_ctx: Optional[int] = None,
    ) -> Dict[str, Any]:
        model = model or self.chat_model
        options: Dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if num_ctx:
            options["num_ctx"] = num_ctx
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if format is not None:
            kwargs["format"] = format
        if options:
            kwargs["options"] = options
        response: Optional[Dict[str, Any]] = None
        error = None
        with llm_log.Timer() as timer:
            try:
                raw = self.client.chat(**kwargs)
                response = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
            except Exception as e:
                error = str(e)
                raise
            finally:
                logged = [
                    dict(m, images=[f"<image, {len(str(i))} bytes>" for i in m["images"]])
                    if m.get("images")
                    else m
                    for m in messages
                ]
                llm_log.log_llm_call(
                    kind,
                    model,
                    logged,
                    response=response,
                    error=error,
                    duration_ms=timer.elapsed_ms,
                    tools=tools,
                )
        return response

    def structured(
        self,
        messages: List[Dict[str, Any]],
        schema: Type[T],
        kind: str,
        model: Optional[str] = None,
        retries: int = 2,
        num_ctx: Optional[int] = None,
        temperature: float = 0.0,
    ) -> T:
        """Ask for JSON matching `schema`; on invalid output, show the model the error and retry."""
        convo = list(messages)
        last_error = ""
        for attempt in range(retries + 1):
            resp = self.chat(
                convo,
                model=model or self.interview_model,
                format=schema.model_json_schema(),
                temperature=temperature,
                kind=kind if attempt == 0 else f"{kind}_retry",
                num_ctx=num_ctx,
            )
            content = (resp.get("message") or {}).get("content") or ""
            try:
                return schema.model_validate_json(content)
            except ValidationError as e:
                last_error = str(e)
                logger.info(
                    f"{kind}: invalid structured output (attempt {attempt + 1}): {last_error[:200]}"
                )
                convo = convo + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That JSON did not match the schema:\n"
                        f"{last_error[:1500]}\nReply again with corrected JSON only.",
                    },
                ]
        raise ValueError(
            f"Model output did not match {schema.__name__} after {retries + 1} tries: {last_error}"
        )


def parse_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning(f"Could not parse tool arguments: {raw[:200]}")
    return {}
