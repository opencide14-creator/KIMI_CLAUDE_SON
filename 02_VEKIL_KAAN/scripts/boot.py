"""
scripts/boot.py — VEKIL-KAAN RAG OS entry point.

Usage:
    python scripts/boot.py

Executes the 5-phase boot sequence and reports results.
Any phase failure exits with code 1 and clear error output.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> int:
    from core.config import get_config
    from core.exceptions import BootFailure, PreflightFailure

    try:
        cfg = get_config()
    except Exception as e:
        print(f"[FATAL] Configuration error: {e}", file=sys.stderr)
        print(f"        Copy .env.example → .env and fill required fields.", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("vekil_kaan.boot")
    log.info("VEKIL-KAAN RAG OS — starting boot sequence")
    log.info("Mode: %s | Vault: %s", cfg.system_mode.value, cfg.obsidian_vault_path)

    from boot.sequence import BootSequence

    seq = BootSequence(cfg)
    try:
        report, ctx = seq.execute()
        print(report.summary())

        if report.success:
            print("\n[BOOT] System ready.")
            print("[BOOT] Phase 5/6: agents will boot here in next phases.")
            return 0
        else:
            print("\n[BOOT] Boot failed. See report above.", file=sys.stderr)
            return 1

    except BootFailure as e:
        print(f"\n[FATAL] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FATAL] Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
