"""llm/ollama.py — Ollama local model interface. Phase 8 target."""
from llm.base import BaseLLMInterface, Message

class OllamaInterface(BaseLLMInterface):
    def __init__(self, host: str, model: str, timeout_s: int = 30):
        self.host = host
        self.model = model
        self.timeout_s = timeout_s
    def complete(self, messages: list[Message], system: str = "") -> str:
        raise NotImplementedError("Phase 8")
    def is_available(self) -> bool:
        raise NotImplementedError("Phase 8")
