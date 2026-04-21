"""
core/hashing.py — Hashing utilities.

BLAKE2b-256: primary hash for bindings and content integrity.
  Python hashlib.blake2b(data, digest_size=32) — identical to the C
  implementation in kernel/blake2b.c (guardian_blake2b_patch.c).

SHA-256: used for fingerprints and content delta checks.

Both are deterministic and produce identical results to:
  - Python: hashlib.blake2b(data, digest_size=32).digest()
  - C:      blake2b_256(out, data, data_len)  [blake2b.h]
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any


# ── BLAKE2b-256 ───────────────────────────────────────────────────────────────

def blake2b_256(data: bytes) -> bytes:
    """
    BLAKE2b with digest_size=32.
    Identical to C blake2b_256() in blake2b.h and Python guardian binding.
    """
    return hashlib.blake2b(data, digest_size=32).digest()


def blake2b_256_hex(data: bytes) -> str:
    return blake2b_256(data).hex()


def blake2b_256_keyed(data: bytes, key: bytes) -> bytes:
    """
    BLAKE2b keyed mode, digest_size=32.
    Identical to C blake2b_256_keyed() in blake2b.h.
    key must be 1-64 bytes.
    """
    if not (1 <= len(key) <= 64):
        raise ValueError(f"BLAKE2b key must be 1-64 bytes, got {len(key)}")
    return hashlib.blake2b(data, digest_size=32, key=key).digest()


# ── Guardian binding ─────────────────────────────────────────────────────────
# Python equivalent of make_binding_blake2b() in guardian_blake2b_patch.c
# binding_input: 70 bytes, exact layout matches C struct

def compute_guardian_binding(
    version:    int,
    counter:    int,
    timestamp:  int,
    g1: float,  g2: float,  g3: float,  g4: float,
    tessa_id:   int,
    kappa_q:    int,
    chaos:      tuple[float, float, float, float],
    from_v:     int,
    to_v:       int,
    domain:     bytes,   # 4 bytes, e.g. b'KRAL'
    flags:      int,
    prev_crc:   int,
    chaos_seed: int,
) -> bytes:
    """
    Computes BLAKE2b-256 binding identical to guardian_blake2b_patch.c
    make_binding_blake2b().

    Layout (70 bytes):
      [0:1]   version       (uint8)
      [1:5]   counter       (uint32 LE)
      [5:13]  timestamp     (uint64 LE)
      [13:17] g1            (float32 LE)
      [17:21] g2            (float32 LE)
      [21:25] g3            (float32 LE)
      [25:29] g4            (float32 LE)
      [29:30] tessa_id      (uint8)
      [30:32] kappa_q       (uint16 LE)
      [32:48] chaos[4]      (4 × float32 LE)
      [48:49] from_v        (uint8)
      [49:50] to_v          (uint8)
      [50:54] domain        (4 bytes)
      [54:58] flags         (uint32 LE)
      [58:62] prev_crc      (uint32 LE)
      [62:70] chaos_seed    (uint64 LE)
      Total: 70 bytes
    """
    assert len(domain) == 4, "domain must be exactly 4 bytes"

    inp = (
        struct.pack("<B",  version)     +   # 1
        struct.pack("<I",  counter)     +   # 4
        struct.pack("<Q",  timestamp)   +   # 8
        struct.pack("<f",  g1)          +   # 4
        struct.pack("<f",  g2)          +   # 4
        struct.pack("<f",  g3)          +   # 4
        struct.pack("<f",  g4)          +   # 4
        struct.pack("<B",  tessa_id)    +   # 1
        struct.pack("<H",  kappa_q)     +   # 2
        struct.pack("<ffff", *chaos)    +   # 16
        struct.pack("<B",  from_v)      +   # 1
        struct.pack("<B",  to_v)        +   # 1
        domain                          +   # 4
        struct.pack("<I",  flags)       +   # 4
        struct.pack("<I",  prev_crc)    +   # 4
        struct.pack("<Q",  chaos_seed)      # 8
    )
    assert len(inp) == 70, f"binding_input length mismatch: {len(inp)}"
    return blake2b_256(inp)


# ── SHA-256 ───────────────────────────────────────────────────────────────────

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's content. Used for delta tracking."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    """SHA-256 of a JSON-serialized object (sorted keys, no whitespace)."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return sha256_hex(data)


# ── Memory root hash ─────────────────────────────────────────────────────────

def compute_root_hash(collection_stats: dict[str, Any], last_event_ids: list[str]) -> str:
    """
    Deterministic root hash over ChromaDB collection metadata + last event IDs.
    Both agents must compute the same value given the same state.
    """
    payload = {
        "collections": collection_stats,
        "last_events": sorted(last_event_ids),  # sort for determinism
    }
    return sha256_json(payload)
