"""llm/claude.py — Anthropic Claude API interface. Phase 8 target."""
from llm.base import BaseLLMInterface, Message

class ClaudeInterface(BaseLLMInterface):
    def __init__(self, api_key: str, model: str, timeout_s: int = 30):
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
    def complete(self, messages: list[Message], system: str = "") -> str:
        raise NotImplementedError("Phase 8")
    def is_available(self) -> bool:
        raise NotImplementedError("Phase 8")
