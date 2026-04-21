"""
scripts/status.py — Print VEKIL-KAAN system status / prison state.

Usage:
    python scripts/status.py

Shows:
  - Last heartbeat timestamp + interval
  - Last commander feed timestamp
  - Escape attempt count (0 = no attempts yet)
  - Memory root hash
  - ChromaDB collection sizes
  - Active agent statuses
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    # Phase 9: pull real status from SQLite + ChromaDB
    print("=" * 60)
    print("VEKIL-KAAN RAG OS — STATUS")
    print("=" * 60)
    print("Phase 0: skeleton only. No live data yet.")
    print("Phase 9: implement full status readout.")
    print("=" * 60)


if __name__ == "__main__":
    main()
