"""
llm/prompts.py — System prompt construction from law registry.
Phase 8 target.
Prompts are deterministic: same law registry state → same prompt → same hash.
"""
class PromptBuilder:
    def build_reactive_system_prompt(self, registry) -> str:
        raise NotImplementedError("Phase 8")
    def build_heartbeat_system_prompt(self, registry) -> str:
        raise NotImplementedError("Phase 8")
