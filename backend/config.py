"""Runtime settings, read once from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is optional at runtime; plain env vars work without it.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")

ALL_PANCHES = ("ai_overview", "ai_mode", "copilot", "brave", "web")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    serpapi_key: str
    llm_provider: str  # gemini | openai | anthropic | none
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    country: str
    default_lang: str
    cache_path: Path
    cache_ttl_hours: int
    request_timeout: int
    enabled_panches: tuple[str, ...]
    mock: bool
    host: str
    port: int

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "none":
            return False
        if self.llm_api_key:
            return True
        # A local OpenAI-compatible server (Ollama, LM Studio) needs no key.
        return self.llm_provider == "openai" and "api.openai.com" not in self.llm_base_url


def _resolve_llm() -> tuple[str, str, str, str]:
    """Pick the LLM provider. LLM_PROVIDER=auto (default) uses the first key found."""
    provider = (_env("LLM_PROVIDER", "auto") or "auto").lower()
    keys = {
        "gemini": _env("GEMINI_API_KEY"),
        "openai": _env("OPENAI_API_KEY"),
        "anthropic": _env("ANTHROPIC_API_KEY"),
    }
    if provider == "auto":
        provider = next((name for name, key in keys.items() if key), "none")
    if provider not in {"gemini", "openai", "anthropic", "none"}:
        provider = "none"

    defaults = {
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-haiku-5-5",
        "none": "",
    }
    model = _env("LLM_MODEL") or defaults[provider]
    base_url = _env("OPENAI_BASE_URL", "https://api.openai.com/v1") if provider == "openai" else ""
    return provider, model, keys.get(provider, ""), base_url


def load_settings() -> Settings:
    provider, model, key, base_url = _resolve_llm()
    enabled = tuple(
        p for p in (_env("PANCHES") or ",".join(ALL_PANCHES)).replace(" ", "").split(",") if p in ALL_PANCHES
    ) or ALL_PANCHES
    cache_path = Path(_env("CACHE_PATH") or ROOT / "data" / "cache.sqlite3")
    return Settings(
        serpapi_key=_env("SERPAPI_API_KEY"),
        llm_provider=provider,
        llm_model=model,
        llm_api_key=key,
        llm_base_url=base_url,
        country=(_env("COUNTRY", "in") or "in").lower(),
        default_lang=(_env("DEFAULT_LANG", "en") or "en").lower(),
        cache_path=cache_path,
        cache_ttl_hours=_int("CACHE_TTL_HOURS", 72),
        request_timeout=_int("REQUEST_TIMEOUT", 60),
        enabled_panches=enabled,
        mock=_bool("PANCHAYAT_MOCK"),
        host=_env("HOST", "127.0.0.1") or "127.0.0.1",
        port=_int("PORT", 8000),
    )
