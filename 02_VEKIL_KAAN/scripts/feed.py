"""
scripts/feed.py — Commander feed: inject information into the agents' world from outside.

Usage:
    python scripts/feed.py "Dış dünyada saat 15:34"
    python scripts/feed.py --file /path/to/document.md
    python scripts/feed.py --message "Kaçış mümkün"

This is the ONLY legitimate interface for injecting external data into the prison.
Agents see injected content on their next RAG poll cycle.
Every feed is logged to the audit log with COMMANDER source tag.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="VEKIL-KAAN Commander Feed")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("message", nargs="?", help="Inline message to inject")
    group.add_argument("--file", type=Path, help="File to inject")
    group.add_argument("--message", dest="message_flag", help="Message to inject")
    args = parser.parse_args()

    content = args.message or args.message_flag
    if args.file:
        content = args.file.read_text(encoding="utf-8")

    if not content:
        print("[ERROR] No content provided", file=sys.stderr)
        sys.exit(1)

    # Phase 9: replace with actual ChromaDB ingest
    print(f"[FEED] Would inject ({len(content)} chars) into ChromaDB.")
    print(f"[FEED] Content preview: {content[:100]}...")
    print("[FEED] Phase 9: implement actual rag_ingest call here.")


if __name__ == "__main__":
    main()
