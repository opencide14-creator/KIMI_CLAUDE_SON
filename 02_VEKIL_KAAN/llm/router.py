"""llm/router.py — Model router: selects LLM per agent based on config."""
from core.config import LLMProvider, LLMConfig
from llm.base import BaseLLMInterface

def build_llm(cfg: LLMConfig) -> BaseLLMInterface:
    if cfg.provider == LLMProvider.OLLAMA:
        from llm.ollama import OllamaInterface
        return OllamaInterface(cfg.ollama_host, cfg.model, cfg.timeout_seconds)
    elif cfg.provider == LLMProvider.CLAUDE:
        from llm.claude import ClaudeInterface
        return ClaudeInterface(cfg.anthropic_api_key, cfg.model, cfg.timeout_seconds)
    raise ValueError(f"Unknown LLM provider: {cfg.provider}")
