#!/usr/bin/env python3
"""
Cryptographic Seal Verification
Verifies Ed25519 law registry seal and HMAC event signatures.
"""
import sys
import hashlib
import json
from pathlib import Path

def verify_registry_seal(registry_path, public_key_path):
    """Verify law registry Ed25519 seal."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        with open(registry_path, 'rb') as f:
            data = f.read()

        # Extract seal (last 64 bytes for Ed25519 sig)
        payload, seal = data[:-64], data[-64:]

        with open(public_key_path, 'rb') as f:
            pub_key = Ed25519PublicKey.from_public_bytes(f.read())

        try:
            pub_key.verify(seal, payload)
            return True, hashlib.sha256(payload).hexdigest()[:16]
        except InvalidSignature:
            return False, "INVALID_SIGNATURE"
    except Exception as e:
        return False, str(e)

def verify_event_hmac(event_path, secret):
    """Verify HMAC-SHA256 event signature."""
    import hmac

    try:
        with open(event_path, 'r') as f:
            event = json.load(f)

        stored_sig = event.pop('_signature', None)
        if not stored_sig:
            return False, "NO_SIGNATURE"

        computed = hmac.new(
            secret.encode(),
            json.dumps(event, sort_keys=True).encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(stored_sig, computed), computed[:16]
    except Exception as e:
        return False, str(e)

def main():
    plugin_root = Path(__file__).parent.parent

    print("🔒 SEAL VERIFICATION")
    print("=" * 50)

    # Check law files exist
    laws_dir = plugin_root / "agents" / "docs"
    if not laws_dir.exists():
        laws_dir = plugin_root / "laws"

    law_files = list(laws_dir.glob("*.md")) if laws_dir.exists() else []
    print(f"Law files found: {len(law_files)}")
    for lf in law_files:
        h = hashlib.sha256(lf.read_bytes()).hexdigest()[:16]
        print(f"  {lf.name}: {h}")

    # Verify registry if exists
    registry = plugin_root / "data" / "law-registry.json"
    pub_key = plugin_root / "keys" / "kral.pub"

    if registry.exists() and pub_key.exists():
        ok, hash_val = verify_registry_seal(registry, pub_key)
        status = "✅ VALID" if ok else "❌ INVALID"
        print(f"\nRegistry seal: {status} | Hash: {hash_val}")
    else:
        print("\nRegistry not sealed yet (boot required)")

    # Check event store
    event_store = plugin_root / "data" / "events"
    if event_store.exists():
        events = list(event_store.glob("*.json"))
        print(f"\nEvents: {len(events)}")

    print("\n🔒 VERIFICATION COMPLETE")

if __name__ == '__main__':
    main()
