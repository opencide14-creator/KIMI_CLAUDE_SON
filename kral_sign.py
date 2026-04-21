"""
KRAL Guardian Signer — SIGNING ONLY.
Input: a JSON file.
Action: reads content, computes HMAC-SHA256 using KRAL fingerprint, adds signature fields.
"""
import json
import hmac
import hashlib
import sys
import datetime

KRAL_FP = "629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52"

def sign(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    sig = hmac.new(KRAL_FP.encode(), raw.encode(), hashlib.sha256).hexdigest()
    data = json.loads(raw)
    data["kral_sig"] = sig
    data["kral_fp"] = KRAL_FP
    data["signed_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"SIGNED: {sig}")

if __name__ == "__main__":
    sign(sys.argv[1])
