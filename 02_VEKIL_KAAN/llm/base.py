"""llm/base.py — BaseLLMInterface ABC."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Message:
    role: str   # "user" | "assistant" | "system"
    content: str

class BaseLLMInterface(ABC):
    @abstractmethod
    def complete(self, messages: list[Message], system: str = "") -> str: ...
    @abstractmethod
    def is_available(self) -> bool: ...
