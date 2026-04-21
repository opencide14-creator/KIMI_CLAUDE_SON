"""
tools/registry.py — Tool registry.
Locked after boot. Post-lock registration raises ToolRegistryLocked.
"""
from core.exceptions import ToolRegistryLocked, ToolNotFound

class ToolRegistry:
    def __init__(self): 
        self._tools: dict = {}
        self._locked = False
    def register(self, tool) -> None:
        if self._locked: raise ToolRegistryLocked("Tool registry is locked")
        self._tools[tool.name] = tool
    def lock(self) -> None: self._locked = True
    def get(self, name: str):
        if name not in self._tools: raise ToolNotFound(f"Tool not found: {name}")
        return self._tools[name]
    def list_names(self) -> list[str]: return list(self._tools.keys())
