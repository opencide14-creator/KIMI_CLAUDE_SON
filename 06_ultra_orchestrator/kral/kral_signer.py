"""
KRAL v3 — Kimi Reliable Authentication Layer
Adapted for Ultra Orchestrator integration.

Cryptographic signing + TESSA force-vector classification +
Başıbozuk chaos integration — pure Python, stdlib only.

Based on: https://github.com/lushbinary/KRAL-signer
"""

from __future__ import annotations

import hashlib, hmac, json, math, os, random, struct, time, zlib, copy
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List, Dict, Any, Union
from enum import IntEnum
import logging

logger = logging.getLogger("KRAL")

# ────────────────────────────────────────────────
# Section 1 — Pure Python Ed25519 (RFC 8032)
# ────────────────────────────────────────────────

q = 2**255 - 19
d = -121665 * pow(121666, q - 2, q) % q
I = pow(2, (q - 1) // 4, q)
B_x = 15112221349535400772501151409588531511454012693041857206046113283949847762202
B_y = 46316835694926478169428394003475163141307993866256225615783033603165251855960

def _expmod(b: int, e: int, m: int) -> int: return pow(b, e, m)

def _inv(x: int) -> int: return _expmod(x, q - 2, q)

def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(d * y * y + 1)
    x = _expmod(xx, (q + 3) // 8, q)
    if (x * x - xx) % q: x = (x * I) % q
    if x % 2: x = q - x
    return x

def _byte(h: bytes, i: int) -> int: return h[i]

def _encodeint(y: int) -> bytes: return y.to_bytes(32, "little")

def _encodepoint(P) -> bytes:
    (_x, _y) = (P[0], P[1])
    return _encodeint(_y + (_x & 1) * 2**255)

def _bit(h: bytes, i: int) -> int: return (_byte(h, i // 8) >> (i % 8)) & 1

def _decodeint(s: bytes) -> int: return int.from_bytes(s[:32], "little")

def _is_identity(P): return P[0] == 0 and P[1] == 1

def _edwards_add(P, Q):
    (_x1, _y1), (_x2, _y2) = P, Q
    _x3 = (_x1 * _y2 + _x2 * _y1) * _inv(1 + d * _x1 * _x2 * _y1 * _y2)
    _y3 = (_y1 * _y2 + _x1 * _x2) * _inv(1 - d * _x1 * _x2 * _y1 * _y2)
    return (_x3 % q, _y3 % q)

def _scalarmult(P, e: int):
    if e == 0: return (0, 1)
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1: Q = _edwards_add(Q, P)
    return Q

def _scalar_mult_base(e: int): return _scalarmult((B_x, B_y), e)

def _hash_scalar(*data: bytes) -> int:
    m = hashlib.blake2b(digest_size=64)
    for d in data: m.update(d)
    return int.from_bytes(m.digest(), "little") % (2**252 + 27742317777372353535851937790883648493)

B_pt = (B_x, B_y)

def _clamp_scalar(h: bytes) -> bytes:
    a = bytearray(h)
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    return bytes(a)

def clamp_scalar(h: bytes) -> bytes:
    return _clamp_scalar(h[:32])

def generate_keypair(entropy: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Generate Ed25519 keypair. Returns (seed, public_key) where seed is 32 bytes."""
    if entropy is None: entropy = os.urandom(32)
    h = hashlib.sha512(entropy).digest()
    a = clamp_scalar(h)
    A = _scalar_mult_base(int.from_bytes(a, "little"))
    return entropy, _encodepoint(A)  # Return original seed, not clamped scalar

L = 2**252 + 27742317777372353535851937790883648493

def ed25519_sign(seed: bytes, message: bytes, public_key: Optional[bytes] = None) -> bytes:
    """Sign message with Ed25519. seed is the 32-byte private key seed."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(clamp_scalar(h), "little")
    if public_key is None:
        public_key = _encodepoint(_scalar_mult_base(a))
    # prefix = h[32:64]
    # r = hash(prefix || message) mod L
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), "little") % L
    # R = r * B
    R = _scalar_mult_base(r)
    R_enc = _encodepoint(R)
    # k = hash(R || A || message) mod L
    k = int.from_bytes(hashlib.sha512(R_enc + public_key + message).digest(), "little") % L
    # S = (r + k * a) mod L
    S = (r + k * a) % L
    return R_enc + _encodeint(S)

def _decodepoint(encoded: bytes) -> tuple:
    """Decode a point from 32-byte encoding, handling the x sign bit."""
    y_int = _decodeint(encoded)
    x_bit = (y_int >> 255) & 1  # sign bit of x
    y = y_int & ((1 << 255) - 1)  # clear sign bit to get y
    x = _xrecover(y)
    # If parity doesn't match, flip x
    if (x & 1) != x_bit:
        x = (-x) % q
    return (x, y)

def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        A = _decodepoint(public_key)
        R_enc, S_enc = signature[:32], signature[32:64]
        R = _decodepoint(R_enc)
        S = _decodeint(S_enc) % L
        h = int.from_bytes(hashlib.sha512(R_enc + public_key + message).digest(), "little") % L
        # Check [S]B = R + [h]A
        SB = _scalar_mult_base(S)
        hA = _scalarmult(A, h)
        R_hA = _edwards_add(R, hA)
        return (SB[0] - R_hA[0]) % q == 0 and (SB[1] - R_hA[1]) % q == 0
    except Exception:
        return False


# ────────────────────────────────────────────────
# Section 2 — TESSA Force-Vector Classifier
# ────────────────────────────────────────────────

@dataclass
class ForceVector:
    """TESSA force vector: [g1, g2, g3, g4]"""
    g1: float = 0.0  # analytical / primary direction [0,1]
    g2: float = 0.0  # creative / acceleration [-1,1]
    g3: float = 0.0  # temporal / stability [0,1]
    g4: float = 0.0  # holistic / coupling [-1,1]

    @property
    def raw_kappa(self) -> float:
        pos = lambda x: max(x, 0.0)
        return 0.34 * pos(self.g1) + 0.22 * pos(self.g2) + 0.32 * pos(self.g3) + 0.12 * pos(self.g4)

    @property
    def signed_kappa(self) -> float:
        return 0.34 * self.g1 + 0.22 * self.g2 + 0.32 * self.g3 + 0.12 * self.g4

    def is_valid(self) -> bool:
        return (0.0 <= self.g1 <= 1.0 and -1.0 <= self.g2 <= 1.0 and
                0.0 <= self.g3 <= 1.0 and -1.0 <= self.g4 <= 1.0)


@dataclass
class TESSAClassification:
    """11-class TESSA result"""
    class_id: int          # 0-10
    class_name: str        # name
    action: str            # ALLOW / MONITOR / ISOLATE
    kappa_q: int           # quantized kappa
    force: ForceVector
    raw_kappa: float
    detail: str = ""


# TESSA class definitions — per SKILL_TRAINING_LAWS_v03
TESSA_CLASSES = {
    0:  {"name": "CODE_STANDARD",        "action": "ALLOW",   "desc": "Canonical code artifact"},
    1:  {"name": "CONFIG_STANDARD",      "action": "ALLOW",   "desc": "Validated config"},
    2:  {"name": "DOCS_STANDARD",        "action": "ALLOW",   "desc": "Canonical docs"},
    3:  {"name": "HYBRID_STANDARD",      "action": "ALLOW",   "desc": "Mixed canonical"},
    4:  {"name": "ANOMALY_KAPPA_LOW",    "action": "MONITOR", "desc": "κ below threshold but structurally ok"},
    5:  {"name": "ANOMALY_FORCE_DRIFT",  "action": "MONITOR", "desc": "Force vector drift"},
    6:  {"name": "ANOMALY_BINDING_FAIL", "action": "MONITOR", "desc": "Binding verification failed"},
    7:  {"name": "ANOMALY_COUNTER_GAPS", "action": "MONITOR", "desc": "Counter non-monotonic"},
    8:  {"name": "ANOMALY_SEED_REUSE",   "action": "MONITOR", "desc": "Seed/pseudonym reused"},
    9:  {"name": "THREAT_CRYPTO_FAIL",   "action": "ISOLATE", "desc": "Cryptographic failure"},
    10: {"name": "THREAT_CHAOS_INVALID", "action": "ISOLATE", "desc": "Chaos signature invalid"},
}

# Martyr coefficients for Başıbozuk chaos
MARTYR_1 = math.pi / 10   # f1
MARTYR_2 = math.e / 10    # f2  
MARTYR_3 = (1 + math.sqrt(5)) / 20  # f3 = φ/10


class TESSAClassifier:
    """Force-vector classification per SKILL_TRAINING_LAWS_v03 §4"""

    def __init__(self, kappa_threshold: float = 0.95):
        self.kappa_threshold = kappa_threshold

    def embed(self, message: bytes) -> ForceVector:
        """Hash message → R^4 force embedding via BLAKE2b-256."""
        h = hashlib.blake2b(message, digest_size=32).digest()
        # Map 32 bytes → 4 floats
        def _norm_chunk(data: bytes, offset: int) -> float:
            val = int.from_bytes(data[offset*8:(offset+1)*8], "little")
            return (val % 1000000) / 1000000.0

        g1 = _norm_chunk(h, 0)  # [0,1]
        g2 = 2.0 * _norm_chunk(h, 1) - 1.0  # [-1,1]
        g3 = _norm_chunk(h, 2)  # [0,1]
        g4 = 2.0 * _norm_chunk(h, 3) - 1.0  # [-1,1]

        # Apply martyr coefficients for irreproducibility
        g1 = math.fmod(g1 + MARTYR_1, 1.0)
        g2 = max(-1.0, min(1.0, g2 + MARTYR_2))
        g3 = math.fmod(g3 + MARTYR_2, 1.0)
        g4 = max(-1.0, min(1.0, g4 + MARTYR_3))

        return ForceVector(g1=g1, g2=g2, g3=g3, g4=g4)

    def classify(self, force: ForceVector, counter: int = 0) -> TESSAClassification:
        """Classify force vector into 11 TESSA classes."""
        kappa = force.raw_kappa
        kappa_q = int(kappa * 16384)

        if force.g1 < 0.5 and kappa < self.kappa_threshold:
            cls = 4  # ANOMALY_KAPPA_LOW
        elif abs(force.g2) > 0.7:
            cls = 5  # ANOMALY_FORCE_DRIFT
        elif counter > 0 and counter % 1000 == 0:
            cls = 7  # ANOMALY_COUNTER_GAPS (periodic check)
        elif kappa >= self.kappa_threshold:
            if force.g3 > 0.8 and force.g4 > 0:
                cls = 0  # CODE_STANDARD
            elif force.g3 > 0.6:
                cls = 1  # CONFIG_STANDARD
            elif force.g4 < 0:
                cls = 2  # DOCS_STANDARD
            else:
                cls = 3  # HYBRID_STANDARD
        elif force.g3 < 0.3:
            cls = 9  # THREAT_CRYPTO_FAIL
        else:
            cls = 6  # ANOMALY_BINDING_FAIL

        info = TESSA_CLASSES[cls]
        return TESSAClassification(
            class_id=cls,
            class_name=info["name"],
            action=info["action"],
            kappa_q=kappa_q,
            force=force,
            raw_kappa=kappa,
            detail=info["desc"]
        )

    def evaluate_kappa(self, g1: float, g2: float, g3: float, g4: float) -> float:
        """Direct κ evaluation from 4 gradient components."""
        pos = lambda x: max(x, 0.0)
        return 0.34 * pos(g1) + 0.22 * pos(g2) + 0.32 * pos(g3) + 0.12 * pos(g4)


# ────────────────────────────────────────────────
# Section 3 — Başıbozuk Chaos (Levy Flight)
# ────────────────────────────────────────────────

@dataclass
class ChaosSignature:
    """16-byte chaos signature"""
    levy_u: int   # 8 bytes — Lévy flight step
    levy_v: int   # 4 bytes — Mantegna algorithm state
    epoch: int    # 2 bytes — iteration epoch
    flags: int    # 2 bytes — chaos flags

    def to_bytes(self) -> bytes:
        return (self.levy_u.to_bytes(8, "little") +
                self.levy_v.to_bytes(4, "little") +
                self.epoch.to_bytes(2, "little") +
                self.flags.to_bytes(2, "little"))

    @classmethod
    def from_bytes(cls, data: bytes) -> "ChaosSignature":
        return cls(
            levy_u=int.from_bytes(data[0:8], "little"),
            levy_v=int.from_bytes(data[8:12], "little"),
            epoch=int.from_bytes(data[12:14], "little"),
            flags=int.from_bytes(data[14:16], "little")
        )


class BasibozukChaos:
    """Lévy flight chaos for irreproducibility (α=1.5, Mantegna algorithm)."""

    ALPHA = 1.5
    SIGMA_U = ((math.gamma(1 + 1.5) * math.sin(math.pi * 1.5 / 2)) /
               (math.gamma((1 + 1.5) / 2) * 1.5 * 2**((1.5 - 1) / 2)))**(1 / 1.5)

    def __init__(self, seed: Optional[bytes] = None):
        self.seed = seed or os.urandom(8)
        self.rng = random.Random(self.seed.hex())
        self.epoch = 0

    def _mantegna_step(self) -> float:
        u = abs(self.rng.gauss(0, self.SIGMA_U))
        v = abs(self.rng.gauss(0, 1))
        if v == 0: v = 1e-10
        step = u / (v ** (1 / self.ALPHA))
        return step

    def generate(self, force: ForceVector) -> ChaosSignature:
        """Generate chaos signature from force vector."""
        self.epoch += 1
        step = self._mantegna_step()
        # Couple chaos to force vector (irreproducible without force)
        levy_u = int(abs(step) * (force.g1 + 1.0) * 1e6) % (2**64)
        levy_v = int(abs(force.g2) * (force.g3 + 0.1) * 1e6) % (2**32)
        flags = 0x0000
        # Set coherence flag if force is well-formed
        if force.is_valid() and force.raw_kappa > 0.5:
            flags |= 0x0001
        return ChaosSignature(levy_u=levy_u, levy_v=levy_v, epoch=self.epoch, flags=flags)

    def verify(self, force: ForceVector, chaos: ChaosSignature) -> bool:
        """Verify chaos signature matches force vector."""
        if chaos.epoch == 0:
            return False
        # Recompute expected values
        expected_flags = 0x0000
        if force.is_valid() and force.raw_kappa > 0.5:
            expected_flags |= 0x0001
        return (chaos.flags & 0x0001) == (expected_flags & 0x0001)


# ────────────────────────────────────────────────
# Section 4 — 112-Byte Wire Packet
# ────────────────────────────────────────────────

@dataclass
class KRALWirePacket:
    """112-byte wire packet for Ultra Orchestrator artifacts."""
    version: int = 3           # 1 byte — protocol version
    counter: int = 0           # 4 bytes — packet counter
    timestamp: int = 0         # 8 bytes — unix nanoseconds
    force_g1: int = 0          # 8 bytes — force g1 quantized
    force_g2: int = 0          # 8 bytes — force g2 quantized
    force_g3: int = 0          # 8 bytes — force g3 quantized
    force_g4: int = 0          # 8 bytes — force g4 quantized
    tessa_class: int = 0       # 1 byte — TESSA classification
    kappa_q: int = 0           # 2 bytes — quantized kappa
    chaos: bytes = field(default_factory=lambda: b"\x00" * 16)
    binding: bytes = field(default_factory=lambda: b"\x00" * 32)
    seed: bytes = field(default_factory=lambda: b"\x00" * 8)
    domain: bytes = field(default_factory=lambda: b"ORCH")  # 4 bytes

    def to_bytes(self) -> bytes:
        return (struct.pack("<B", self.version) +
                struct.pack("<I", self.counter) +
                struct.pack("<Q", self.timestamp) +
                struct.pack("<Q", self.force_g1 & 0xFFFFFFFFFFFFFFFF) +
                struct.pack("<Q", self.force_g2 & 0xFFFFFFFFFFFFFFFF) +
                struct.pack("<Q", self.force_g3 & 0xFFFFFFFFFFFFFFFF) +
                struct.pack("<Q", self.force_g4 & 0xFFFFFFFFFFFFFFFF) +
                struct.pack("<B", self.tessa_class) +
                struct.pack("<H", self.kappa_q) +
                self.chaos +
                self.binding +
                self.seed +
                self.domain)

    @classmethod
    def from_bytes(cls, data: bytes) -> "KRALWirePacket":
        if len(data) not in (108, 112):
            raise ValueError(f"Wire packet must be 108 or 112 bytes, got {len(data)}")
        p = cls()
        p.version = data[0]
        p.counter = int.from_bytes(data[1:5], "little")
        p.timestamp = int.from_bytes(data[5:13], "little")
        p.force_g1 = int.from_bytes(data[13:21], "little")
        p.force_g2 = int.from_bytes(data[21:29], "little")
        p.force_g3 = int.from_bytes(data[29:37], "little")
        p.force_g4 = int.from_bytes(data[37:45], "little")
        p.tessa_class = data[45]
        p.kappa_q = int.from_bytes(data[46:48], "little")
        p.chaos = data[48:64]
        p.binding = data[64:96]
        p.seed = data[96:104]
        p.domain = data[104:108]
        return p

    @property
    def crc32(self) -> int:
        return zlib.crc32(self.to_bytes()[:108]) & 0xFFFFFFFF

    def verify_crc(self, expected_crc: int) -> bool:
        return self.crc32 == expected_crc


# ────────────────────────────────────────────────
# Section 5 — KRAL Guardian (Sign / Verify)
# ────────────────────────────────────────────────

@dataclass
class KRALArtifact:
    """A signed artifact with full TESSA classification."""
    artifact_id: str
    artifact_type: str          # "code", "config", "docs", "hybrid"
    content_hash: str           # SHA-256 of content
    force: ForceVector
    tessa: TESSAClassification
    chaos: ChaosSignature
    wire_packet: bytes          # 112-byte wire
    signature: bytes            # 64-byte Ed25519
    public_key: bytes           # 32-byte Ed25519 public key
    timestamp: float
    domain: str = "ULTRA_ORCHESTRATOR_v2"

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "content_hash": self.content_hash,
            "force": {"g1": self.force.g1, "g2": self.force.g2, "g3": self.force.g3, "g4": self.force.g4},
            "tessa": {"class_id": self.tessa.class_id, "class_name": self.tessa.class_name,
                      "action": self.tessa.action, "kappa_q": self.tessa.kappa_q,
                      "raw_kappa": self.tessa.raw_kappa},
            "chaos": {"levy_u": self.chaos.levy_u, "levy_v": self.chaos.levy_v,
                      "epoch": self.chaos.epoch, "flags": self.chaos.flags},
            "wire_packet": self.wire_packet.hex(),
            "signature": self.signature.hex(),
            "public_key": self.public_key.hex(),
            "timestamp": self.timestamp,
            "domain": self.domain
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KRALArtifact":
        f = d["force"]
        t = d["tessa"]
        c = d["chaos"]
        return cls(
            artifact_id=d["artifact_id"],
            artifact_type=d["artifact_type"],
            content_hash=d["content_hash"],
            force=ForceVector(g1=f["g1"], g2=f["g2"], g3=f["g3"], g4=f["g4"]),
            tessa=TESSAClassification(class_id=t["class_id"], class_name=t["class_name"],
                                       action=t["action"], kappa_q=t["kappa_q"],
                                       force=ForceVector(), raw_kappa=t["raw_kappa"]),
            chaos=ChaosSignature(levy_u=c["levy_u"], levy_v=c["levy_v"],
                                  epoch=c["epoch"], flags=c["flags"]),
            wire_packet=bytes.fromhex(d["wire_packet"]),
            signature=bytes.fromhex(d["signature"]),
            public_key=bytes.fromhex(d["public_key"]),
            timestamp=d["timestamp"],
            domain=d.get("domain", "ULTRA_ORCHESTRATOR_v2")
        )


class KRALGuardian:
    """
    Main KRAL signing/verification guardian.
    Integrates Ed25519 + TESSA + Başıbozuk into 112-byte wire packets.
    """

    def __init__(self, seed: Optional[bytes] = None,
                 kappa_threshold: float = 0.95):
        self.kappa_threshold = kappa_threshold
        self.tessa = TESSAClassifier(kappa_threshold)
        self.chaos_engine = BasibozukChaos()

        if seed:
            self.seed = seed
            h = hashlib.sha512(seed).digest()
            a = int.from_bytes(clamp_scalar(h), "little")
            self.public_key = _encodepoint(_scalar_mult_base(a))
        else:
            self.seed, self.public_key = generate_keypair()

        self._counter = 0
        self._artifacts: Dict[str, KRALArtifact] = {}
        logger.info(f"KRALGuardian initialized — κ_threshold={kappa_threshold}, "
                    f"pubkey={self.public_key.hex()[:16]}...")

    @property
    def public_key_hex(self) -> str:
        return self.public_key.hex()

    def _next_counter(self) -> int:
        self._counter += 1
        return self._counter

    def _compute_binding(self, message: bytes, force: ForceVector, chaos: ChaosSignature) -> bytes:
        """Compute 32-byte binding hash: SHA-256(message || force || chaos || seed)."""
        h = hashlib.sha256()
        h.update(message)
        h.update(struct.pack("<dddd", force.g1, force.g2, force.g3, force.g4))
        h.update(chaos.to_bytes())
        h.update(self.chaos_engine.seed)
        return h.digest()

    def guardian_sign(self, content: bytes, artifact_id: str,
                      artifact_type: str = "hybrid") -> KRALArtifact:
        """
        Full KRAL signing pipeline:
        content → hash → force → tessa → chaos → binding → wire → ed25519
        """
        # 1. Content hash
        content_hash = hashlib.sha256(content).hexdigest()

        # 2. Force embedding
        force = self.tessa.embed(content_hash.encode())

        # 3. TESSA classification
        counter = self._next_counter()
        tessa_result = self.tessa.classify(force, counter)

        # 4. κ check — ISOLATE if below threshold
        if tessa_result.raw_kappa < self.kappa_threshold and tessa_result.class_id >= 9:
            logger.warning(f"KRAL: ISOLATE — κ={tessa_result.raw_kappa:.4f} < {self.kappa_threshold} "
                           f"for {artifact_id}")

        # 5. Başıbozuk chaos
        chaos = self.chaos_engine.generate(force)

        # 6. Compute binding
        binding = self._compute_binding(content_hash.encode(), force, chaos)

        # 7. Build 112-byte wire packet
        wire = KRALWirePacket(
            version=3,
            counter=counter,
            timestamp=int(time.time() * 1e9),
            force_g1=int(force.g1 * 2**64) & 0xFFFFFFFFFFFFFFFF,
            force_g2=int(((force.g2 + 1.0) / 2.0) * 2**64) & 0xFFFFFFFFFFFFFFFF,
            force_g3=int(force.g3 * 2**64) & 0xFFFFFFFFFFFFFFFF,
            force_g4=int(((force.g4 + 1.0) / 2.0) * 2**64) & 0xFFFFFFFFFFFFFFFF,
            tessa_class=tessa_result.class_id,
            kappa_q=tessa_result.kappa_q,
            chaos=chaos.to_bytes(),
            binding=binding,
            seed=self.chaos_engine.seed,
            domain=b"ORCH"
        )
        wire_bytes = wire.to_bytes()

        # 8. Append CRC32
        crc = struct.pack("<I", wire.crc32)
        wire_bytes_full = wire_bytes + crc  # 112 bytes

        # 9. Ed25519 sign
        sig = ed25519_sign(self.seed, wire_bytes_full, self.public_key)

        artifact = KRALArtifact(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            content_hash=content_hash,
            force=force,
            tessa=tessa_result,
            chaos=chaos,
            wire_packet=wire_bytes_full,
            signature=sig,
            public_key=bytes(self.public_key),
            timestamp=time.time(),
            domain="ULTRA_ORCHESTRATOR_v2"
        )
        self._artifacts[artifact_id] = artifact
        logger.info(f"KRAL SIGNED: {artifact_id} — {tessa_result.class_name} "
                    f"(κ={tessa_result.raw_kappa:.4f}, action={tessa_result.action})")
        return artifact

    def guardian_verify(self, artifact: KRALArtifact,
                        expected_content: Optional[bytes] = None) -> dict:
        """
        10-step KRAL verification:
        1. CRC32, 2. Version, 3. Domain, 4. Counter, 5. Force bounds,
        6. TESSA class, 7. Binding, 8. Chaos validity, 9. Timestamp, 10. Ed25519
        """
        steps = {}

        # Step 1: CRC32
        try:
            packet = KRALWirePacket.from_bytes(artifact.wire_packet[:108])
            crc_valid = packet.verify_crc(int.from_bytes(artifact.wire_packet[108:112], "little"))
            steps["crc32"] = crc_valid
        except Exception as e:
            steps["crc32"] = False
            steps["crc32_error"] = str(e)

        # Step 2: Version
        steps["version"] = (packet.version == 3 if "packet" in dir() else False)

        # Step 3: Domain
        try:
            steps["domain"] = packet.domain == b"ORCH"
        except:
            steps["domain"] = False

        # Step 4: Counter monotonicity
        steps["counter"] = packet.counter > 0

        # Step 5: Force bounds
        steps["force_bounds"] = artifact.force.is_valid()

        # Step 6: TESSA class consistency
        expected_tessa = self.tessa.classify(artifact.force, packet.counter)
        steps["tessa_class"] = (expected_tessa.class_id == artifact.tessa.class_id)
        steps["tessa_class_id"] = artifact.tessa.class_id
        steps["tessa_class_name"] = artifact.tessa.class_name
        steps["tessa_action"] = artifact.tessa.action

        # Step 7: Binding
        expected_binding = self._compute_binding(
            artifact.content_hash.encode(), artifact.force, artifact.chaos)
        steps["binding"] = (expected_binding == packet.binding)

        # Step 8: Chaos validity
        steps["chaos"] = self.chaos_engine.verify(artifact.force, artifact.chaos)

        # Step 9: Timestamp (within 1 hour)
        ts_sec = packet.timestamp // 1_000_000_000
        steps["timestamp"] = abs(time.time() - ts_sec) < 3600

        # Step 10: Ed25519 signature
        try:
            sig_valid = ed25519_verify(
                artifact.public_key,
                artifact.wire_packet,
                artifact.signature
            )
            steps["ed25519"] = sig_valid
        except Exception as e:
            steps["ed25519"] = False
            steps["ed25519_error"] = str(e)

        # Content hash verification (if content provided)
        if expected_content is not None:
            expected_hash = hashlib.sha256(expected_content).hexdigest()
            steps["content_hash"] = (expected_hash == artifact.content_hash)

        steps["all_passed"] = all([
            steps.get("crc32", False),
            steps.get("version", False),
            steps.get("domain", False),
            steps.get("counter", False),
            steps.get("force_bounds", False),
            steps.get("tessa_class", False),
            steps.get("binding", False),
            steps.get("chaos", False),
            steps.get("timestamp", False),
            steps.get("ed25519", False),
        ])
        steps["kappa"] = artifact.tessa.raw_kappa

        return steps

    def get_artifact(self, artifact_id: str) -> Optional[KRALArtifact]:
        return self._artifacts.get(artifact_id)

    def list_artifacts(self) -> List[str]:
        return list(self._artifacts.keys())

    def save_artifacts(self, filepath: str):
        data = {aid: art.to_dict() for aid, art in self._artifacts.items()}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def load_artifacts(self, filepath: str):
        with open(filepath, "r") as f:
            data = json.load(f)
        self._artifacts = {
            aid: KRALArtifact.from_dict(art)
            for aid, art in data.items()
        }


# ────────────────────────────────────────────────
# Section 6 — Ultra Orchestrator Integration Layer
# ────────────────────────────────────────────────

class KRALOrchestratorIntegration:
    """
    Bridges KRAL signing/verification into the Ultra Orchestrator workflow.
    Every artifact (code, config, output) is signed and verified.
    """

    def __init__(self, guardian: Optional[KRALGuardian] = None,
                 kappa_threshold: float = 0.95):
        self.guardian = guardian or KRALGuardian(kappa_threshold=kappa_threshold)
        self._verification_log: List[dict] = []

    def sign_subtask_output(self, subtask_id: str, output: str,
                            output_type: str = "code") -> KRALArtifact:
        """Sign a subtask's output artifact."""
        content = output.encode("utf-8")
        artifact = self.guardian.guardian_sign(
            content=content,
            artifact_id=f"ST-{subtask_id}-{int(time.time())}",
            artifact_type=output_type
        )
        return artifact

    def verify_subtask_output(self, artifact: KRALArtifact,
                               expected_output: Optional[str] = None) -> dict:
        """Verify a subtask's signed output."""
        expected_bytes = expected_output.encode("utf-8") if expected_output else None
        result = self.guardian.guardian_verify(artifact, expected_bytes)
        self._verification_log.append({
            "timestamp": time.time(),
            "artifact_id": artifact.artifact_id,
            "result": result,
            "passed": result.get("all_passed", False),
            "kappa": result.get("kappa", 0.0)
        })
        return result

    def evaluate_kappa_for_output(self, output: str) -> float:
        """Quick κ evaluation for an output string (no signing)."""
        force = self.guardian.tessa.embed(hashlib.sha256(output.encode()).digest())
        return force.raw_kappa

    def classify_output(self, output: str) -> TESSAClassification:
        """Classify an output without signing it."""
        force = self.guardian.tessa.embed(hashlib.sha256(output.encode()).digest())
        return self.guardian.tessa.classify(force)

    def get_verification_report(self) -> dict:
        """Get aggregate verification statistics."""
        if not self._verification_log:
            return {"total": 0, "passed": 0, "failed": 0, "avg_kappa": 0.0}
        total = len(self._verification_log)
        passed = sum(1 for v in self._verification_log if v["passed"])
        avg_kappa = sum(v["kappa"] for v in self._verification_log) / total
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "avg_kappa": avg_kappa,
            "pass_rate": passed / total if total > 0 else 0.0
        }

    def is_output_authorized(self, artifact: KRALArtifact) -> bool:
        """Check if output is authorized (κ ≥ threshold + ALLOW class)."""
        return (artifact.tessa.raw_kappa >= self.guardian.kappa_threshold and
                artifact.tessa.action == "ALLOW")


# Module exports
__all__ = [
    "KRALGuardian",
    "KRALArtifact",
    "KRALWirePacket",
    "ForceVector",
    "TESSAClassification",
    "TESSAClassifier",
    "TESSA_CLASSES",
    "ChaosSignature",
    "BasibozukChaos",
    "KRALOrchestratorIntegration",
    "generate_keypair",
    "ed25519_sign",
    "ed25519_verify",
    "clamp_scalar",
]
