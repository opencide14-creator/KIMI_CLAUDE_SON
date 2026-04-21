"""
tools/sandbox.py — Tool call execution sandbox.
Phase 9 implementation target.
Every tool call passes through: registry check → law check → escape check → heartbeat log → execute → ingest.
"""
class ToolSandbox:
    def call(self, agent: str, tool_name: str, args: dict):
        raise NotImplementedError("Phase 9")
