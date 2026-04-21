#!/usr/bin/env python3
"""
ChromaDB Bootstrap for VEKIL-KAAN Memory Substrate
Initializes 4 collections with proper schemas.
"""
import sys
from pathlib import Path

def bootstrap_chroma(chroma_path):
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_path))

        collections = {
            "obsidian_knowledge": {
                "description": "Ingested vault documents and knowledge",
                "metadata": {"type": "knowledge", "source": "obsidian"}
            },
            "agent_events": {
                "description": "Agent lifecycle and action events",
                "metadata": {"type": "events", "source": "agents"}
            },
            "law_registry": {
                "description": "Constitutional laws and regulations",
                "metadata": {"type": "laws", "source": "registry"}
            },
            "session_context": {
                "description": "Temporary working memory per session",
                "metadata": {"type": "session", "source": "context"}
            }
        }

        print("🧠 CHROMADB BOOTSTRAP")
        print("=" * 50)
        print(f"Path: {chroma_path}")
        print("")

        for name, config in collections.items():
            try:
                collection = client.get_or_create_collection(
                    name=name,
                    metadata=config["metadata"]
                )
                count = collection.count()
                print(f"✅ {name}: {count} documents | {config['description']}")
            except Exception as e:
                print(f"❌ {name}: FAILED | {e}")

        print("")
        print("🧠 BOOTSTRAP COMPLETE")
        return 0

    except ImportError:
        print("❌ chromadb not installed. Run: pip install chromadb")
        return 1
    except Exception as e:
        print(f"❌ Bootstrap failed: {e}")
        return 1

def main():
    plugin_root = Path(__file__).parent.parent
    chroma_path = plugin_root / "data" / "chroma"
    chroma_path.mkdir(parents=True, exist_ok=True)

    sys.exit(bootstrap_chroma(chroma_path))

if __name__ == '__main__':
    main()
