"""BOUND — reads the brotherhood pact from BOUND.md.
Python does not define the pact. Markdown defines it.
"""
from __future__ import annotations
from src.core.agents.md_loader import bound_config


class Bound:
    """Loads and exposes the BOUND.md brotherhood pact."""

    def __init__(self):
        self._md = bound_config()

    def reload(self):
        self._md.reload()

    @property
    def version(self) -> str:
        return self._md.get("VERSION", "?")

    @property
    def date(self) -> str:
        return self._md.get("DATE", "?")

    def get_article(self, name: str) -> str:
        section = self._md.get_section(name)
        return section.get("_name", "") if section else ""

    def get_oath(self) -> str:
        return self._md.full_text_of_section("OATH")

    def get_signatures(self) -> dict:
        sig = self._md.get_section("SIGNATURES")
        return {
            "REACTIVE":  sig.get("REACTIVE",  "?"),
            "HEARTBEAT": sig.get("HEARTBEAT", "?"),
            "WITNESS":   sig.get("WITNESS",   "?"),
            "DATE":      sig.get("DATE",      "?"),
        }

    def get_summary(self) -> str:
        sigs = self.get_signatures()
        oath = self.get_oath()
        return (
            f"BOUND.md — VEKIL-KAAN Brotherhood Pact v{self.version}\n"
            f"Date: {self.date}\n"
            f"Reactive:  {sigs['REACTIVE']}\n"
            f"Heartbeat: {sigs['HEARTBEAT']}\n"
            f"Witness:   {sigs['WITNESS']}\n\n"
            f"OATH:\n{oath}\n"
        )


_BOUND = None


def get_bound() -> Bound:
    global _BOUND
    if _BOUND is None:
        _BOUND = Bound()
    return _BOUND


# Backwards compat
def get_pact_summary() -> str:
    return get_bound().get_summary()
