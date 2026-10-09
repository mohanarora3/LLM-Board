"""Minimal LLM clients (Gemini, any OpenAI-compatible API, Anthropic) with JSON and streaming calls.

Plain HTTP via `requests` keeps dependencies small. Streaming calls are sync
generators; the pipeline runs them in a worker thread.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

import requests

from .config import Settings


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a model reply, tolerating code fences and chatter."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("Model did not return JSON")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model returned malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model JSON was not an object")
    return value


def _sse_lines(response: requests.Response) -> Iterator[str]:
    for raw in response.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            payload = raw[5:].strip()
            if payload and payload != "[DONE]":
                yield payload


class LLM:
    def __init__(self, settings: Settings, timeout: int = 90) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self.key = settings.llm_api_key
        self.base_url = settings.llm_base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # -- public API -----------------------------------------------------------

    def complete_json(self, system: str, user: str, max_tokens: int = 4096) -> dict[str, Any]:
        return parse_json(self._complete(system, user, max_tokens, json_mode=True))

    def complete_text(self, system: str, user: str, max_tokens: int = 512) -> str:
        return self._complete(system, user, max_tokens, json_mode=False).strip()

    def stream(self, system: str, user: str, max_tokens: int = 4096) -> Iterator[str]:
        handler = {"gemini": self._gemini_stream, "openai": self._openai_stream, "anthropic": self._anthropic_stream}
        yield from handler[self.provider](system, user, max_tokens)

    # -- plumbing -------------------------------------------------------------

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str], stream: bool = False) -> requests.Response:
        try:
            response = self.session.post(url, json=body, headers=headers, timeout=self.timeout, stream=stream)
        except requests.RequestException as exc:
            raise LLMError(f"{self.provider} request failed: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            detail = response.text[:300] if not stream else ""
            raise LLMError(f"{self.provider} HTTP {response.status_code} {detail}".strip())
        return response

    def _complete(self, system: str, user: str, max_tokens: int, json_mode: bool) -> str:
        if self.provider == "gemini":
            config: dict[str, Any] = {"temperature": 0.1, "maxOutputTokens": max_tokens}
            if json_mode:
                config["responseMimeType"] = "application/json"
            data = self._post(*self._gemini_request(system, user, config, stream=False)).json()
            return self._gemini_text(data)
        if self.provider == "openai":
            body: dict[str, Any] = {"model": self.model, "temperature": 0.1, "messages": self._messages(system, user)}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            data = self._post(f"{self.base_url}/chat/completions", body, self._openai_headers()).json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if self.provider == "anthropic":
            body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.1, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            data = self._post("https://api.anthropic.com/v1/messages", body, self._anthropic_headers()).json()
            return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        raise LLMError("No LLM provider configured")

    @staticmethod
    def _messages(system: str, user: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # Gemini
    def _gemini_request(self, system: str, user: str, config: dict[str, Any], stream: bool) -> tuple[str, dict, dict]:
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:{method}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        return url, body, {"x-goog-api-key": self.key, "Content-Type": "application/json"}

    @staticmethod
    def _gemini_text(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))

    def _gemini_stream(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        url, body, headers = self._gemini_request(system, user, {"temperature": 0.2, "maxOutputTokens": max_tokens}, True)
        with self._post(url, body, headers, stream=True) as response:
            for payload in _sse_lines(response):
                text = self._gemini_text(json.loads(payload))
                if text:
                    yield text

    # OpenAI-compatible (OpenAI, Groq, OpenRouter, Ollama, LM Studio …)
    def _openai_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def _openai_stream(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        body = {"model": self.model, "temperature": 0.2, "stream": True, "messages": self._messages(system, user)}
        with self._post(f"{self.base_url}/chat/completions", body, self._openai_headers(), stream=True) as response:
            for payload in _sse_lines(response):
                choices = json.loads(payload).get("choices") or []
                text = (choices[0].get("delta") or {}).get("content") if choices else None
                if text:
                    yield text

    # Anthropic
    def _anthropic_headers(self) -> dict[str, str]:
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def _anthropic_stream(self, system: str, user: str, max_tokens: int) -> Iterator[str]:
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.2, "system": system, "stream": True,
                "messages": [{"role": "user", "content": user}]}
        with self._post("https://api.anthropic.com/v1/messages", body, self._anthropic_headers(), stream=True) as response:
            for payload in _sse_lines(response):
                event = json.loads(payload)
                if event.get("type") == "content_block_delta":
                    text = (event.get("delta") or {}).get("text")
                    if text:
                        yield text
