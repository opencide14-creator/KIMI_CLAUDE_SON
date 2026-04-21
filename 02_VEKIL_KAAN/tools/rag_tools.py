"""
tools/rag_tools.py — RAG-internal tools (prison mode tool set).
Phase 9 implementation target.
"""
from dataclasses import dataclass

@dataclass 
class ToolResult:
    success: bool
    data: object
    error: str = ""

class RagRead:
    name = "rag_read"
    def execute(self, chunk_id: str) -> ToolResult: raise NotImplementedError("Phase 9")

class RagWrite:
    name = "rag_write"
    def execute(self, content: str, metadata: dict) -> ToolResult: raise NotImplementedError("Phase 9")

class RagSearch:
    name = "rag_search"
    def execute(self, query: str, n_results: int = 5) -> ToolResult: raise NotImplementedError("Phase 9")

class RagIngest:
    name = "rag_ingest"
    def execute(self, document: str, source: str) -> ToolResult: raise NotImplementedError("Phase 9")

RAG_TOOL_SET = [RagRead, RagWrite, RagSearch, RagIngest]
