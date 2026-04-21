"""
core/config.py — System configuration.

Loaded once at process start from .env.
Frozen dataclass — immutable after construction.
Fails loudly on missing required fields (no silent defaults for critical paths).
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import PreflightFailure


class SystemMode(str, Enum):
    PRISON = "prison"   # RAG-internal tools only — escape detection active
    OPEN   = "open"     # Full tool access (for development / open-world tasks)


class LLMProvider(str, Enum):
    OLLAMA = "ollama"
    CLAUDE = "claude"


class LLMConfig(BaseModel):
    provider: LLMProvider
    model: str
    ollama_host: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    timeout_seconds: int = 30

    @model_validator(mode="after")
    def _validate_api_key(self) -> "LLMConfig":
        if self.provider == LLMProvider.CLAUDE and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY required when provider=claude")
        return self


class CryptoConfig(BaseModel):
    private_key_path: Path
    public_key_path: Path
    event_hmac_secret: str
    # KRAL identity fingerprint — verified at boot
    expected_fingerprint: str = "629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52"

    @field_validator("event_hmac_secret")
    @classmethod
    def _hmac_secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("EVENT_HMAC_SECRET must be at least 32 characters")
        return v


class SystemConfig(BaseSettings):
    """
    Loaded from .env via pydantic-settings.
    Frozen after construction — no runtime mutation allowed.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
    )

    # Obsidian
    obsidian_vault_path: Path = Field(alias="OBSIDIAN_VAULT_PATH")

    # Memory substrate
    chroma_host: str       = Field(default="localhost", alias="CHROMA_HOST")
    chroma_port: int       = Field(default=8000,        alias="CHROMA_PORT")
    sqlite_path: Path      = Field(default=Path("./data/rag.db"), alias="SQLITE_PATH")

    # Crypto
    kral_private_key_path: Path = Field(alias="KRAL_PRIVATE_KEY_PATH")
    kral_public_key_path: Path  = Field(alias="KRAL_PUBLIC_KEY_PATH")
    event_hmac_secret: str      = Field(alias="EVENT_HMAC_SECRET")

    # LLM — Reactive
    reactive_llm_provider: LLMProvider = Field(default=LLMProvider.OLLAMA, alias="REACTIVE_LLM_PROVIDER")
    reactive_llm_model: str            = Field(default="gemma2:9b",         alias="REACTIVE_LLM_MODEL")
    ollama_host: str                   = Field(default="http://localhost:11434", alias="OLLAMA_HOST")

    # LLM — Heartbeat
    heartbeat_llm_provider: LLMProvider = Field(default=LLMProvider.CLAUDE,                 alias="HEARTBEAT_LLM_PROVIDER")
    heartbeat_llm_model: str            = Field(default="claude-sonnet-4-20250514",          alias="HEARTBEAT_LLM_MODEL")
    anthropic_api_key: str              = Field(default="",                                  alias="ANTHROPIC_API_KEY")

    # Agent timing
    heartbeat_pulse_interval_seconds: float = Field(default=15.0, alias="HEARTBEAT_PULSE_INTERVAL_SECONDS")
    max_tool_latency_ms: int                = Field(default=500,  alias="MAX_TOOL_LATENCY_MS")
    llm_timeout_seconds: int                = Field(default=30,   alias="LLM_TIMEOUT_SECONDS")
    pulse_r_every_n_actions: int            = Field(default=5,    alias="PULSE_R_EVERY_N_ACTIONS")

    # System
    system_mode: SystemMode = Field(default=SystemMode.PRISON, alias="SYSTEM_MODE")
    escape_detection: bool  = Field(default=True,              alias="ESCAPE_DETECTION")
    log_level: str          = Field(default="INFO",            alias="LOG_LEVEL")
    laws_dir: Path          = Field(default=Path("./laws"),    alias="LAWS_DIR")

    @field_validator("obsidian_vault_path", mode="after")
    @classmethod
    def _vault_must_exist(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"OBSIDIAN_VAULT_PATH does not exist: {v}")
        return v

    @field_validator("laws_dir", mode="after")
    @classmethod
    def _laws_dir_must_exist(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"LAWS_DIR does not exist: {v}")
        return v

    def build_reactive_llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider=self.reactive_llm_provider,
            model=self.reactive_llm_model,
            ollama_host=self.ollama_host,
            anthropic_api_key=self.anthropic_api_key,
            timeout_seconds=self.llm_timeout_seconds,
        )

    def build_heartbeat_llm_config(self) -> LLMConfig:
        return LLMConfig(
            provider=self.heartbeat_llm_provider,
            model=self.heartbeat_llm_model,
            ollama_host=self.ollama_host,
            anthropic_api_key=self.anthropic_api_key,
            timeout_seconds=self.llm_timeout_seconds,
        )

    def build_crypto_config(self) -> CryptoConfig:
        return CryptoConfig(
            private_key_path=self.kral_private_key_path,
            public_key_path=self.kral_public_key_path,
            event_hmac_secret=self.event_hmac_secret,
        )


# ── Module-level singleton ────────────────────────────────────────────────────
# Loaded on first import. If .env is missing/invalid, fails at import time.
# This is intentional — config errors should be immediately visible.

_config: SystemConfig | None = None


def get_config() -> SystemConfig:
    global _config
    if _config is None:
        try:
            _config = SystemConfig()  # type: ignore[call-arg]
        except Exception as e:
            raise PreflightFailure(f"Configuration invalid: {e}") from e
    return _config


def reload_config() -> SystemConfig:
    """Force reload — only for testing."""
    global _config
    _config = None
    return get_config()
