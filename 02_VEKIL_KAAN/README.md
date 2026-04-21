# VEKIL-KAAN RAG OS

**Private experiment — not for distribution.**

Dual-agent autonomous system where the RAG environment (ChromaDB + SQLite) 
is the world, not a tool. Obsidian vault as default knowledge substrate.
Markdown laws as executable runtime configuration.

## Identity

- **KRAL** — system owner, Ed25519 signing authority (SDCK-UAGL-v2.0)
- **VEKIL-KAAN** — the two-agent system (Reactive + Heartbeat)
- **RAG** — the world both agents inhabit

## Boot sequence

```
MEMORY → RAG → LAWS → PREFLIGHT → AGENTS
```

Any phase failure = hard stop. No fallbacks.

## Phase status

| Phase | What | Status |
|-------|------|--------|
| 0 | Skeleton + contracts + crypto | ✅ 38/38 tests |
| 1 | Memory substrate | 🔲 next |
| 2 | Markdown law engine | 🔲 |
| 3 | Obsidian ingest | 🔲 |
| 4 | Boot sequence + preflight | 🔲 |
| 5 | Heartbeat agent | 🔲 |
| 6 | Reactive agent | 🔲 |
| 7 | Dual-loop sync | 🔲 |
| 8 | LLM wiring | 🔲 |
| 9 | Tool sandbox + escape detection | 🔲 |
| 10 | Production hardening | 🔲 |

## Quick start

```bash
cp .env.example .env
# edit .env: set OBSIDIAN_VAULT_PATH, KRAL_PRIVATE_KEY_PATH, etc.
pip install -r requirements.txt
python -m pytest tests/ -v
python scripts/boot.py
```

## Key files

- `VEKIL_KAAN_ROADMAP.md` — full system design + key management
- `laws/` — place law markdown files here (SOUL.md, REACT_LOOP.md, etc.)
- `keys/kral_public.pem` — KRAL public key (committed)
- `keys/kral_private.pem` — KRAL private key (NEVER commit — .gitignore)
