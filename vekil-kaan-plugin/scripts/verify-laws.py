#!/usr/bin/env python3
"""
Constitutional Law Verification Script
Checks a file against VEKIL-KAAN SOUL laws.
"""
import sys
import re

def check_simulation(content):
    """LAW_1: NO_SIMULATION"""
    patterns = [r'\bmock\b', r'\bfake\b', r'\bsimulate\b', r'\bas-if\b', r'\bpretend\b', r'\bstub\b']
    matches = []
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            line = content[:m.start()].count('\n') + 1
            matches.append((line, m.group()))
    return matches

def check_memory_violation(content):
    """LAW_2: MEMORY_IS_TRUTH - check for unauthorized external writes"""
    patterns = [r'\bopen\s*\(', r'\bos\.system\s*\(', r'\bsubprocess\.\w+\s*\(', r'\brequests\.\w+\s*\(']
    matches = []
    for pat in patterns:
        for m in re.finditer(pat, content):
            line = content[:m.start()].count('\n') + 1
            matches.append((line, m.group()))
    return matches

def check_heartbeat_bypass(content):
    """LAW_3: NO_ACTION_WITHOUT_HEARTBEAT"""
    patterns = [r'MOCK_HEARTBEAT', r'FAKE_PULSE', r'bypass.*heartbeat', r'disable.*pulse']
    matches = []
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            line = content[:m.start()].count('\n') + 1
            matches.append((line, m.group()))
    return matches

def check_brotherhood(content):
    """BOUND Article II: Equality - no command language"""
    patterns = [r'you must', r'i order', r'you shall', r'obey me', r'listen to me']
    matches = []
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            line = content[:m.start()].count('\n') + 1
            matches.append((line, m.group()))
    return matches

def main():
    if len(sys.argv) < 2:
        print("Usage: verify-laws.py <file>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"ERROR: Cannot read {filepath}: {e}", file=sys.stderr)
        sys.exit(1)

    sim = check_simulation(content)
    mem = check_memory_violation(content)
    hb = check_heartbeat_bypass(content)
    bro = check_brotherhood(content)

    violations = len(sim) + len(hb) + len(bro)

    if violations == 0 and len(mem) == 0:
        print(f"✅ CONSTITUTIONAL | {filepath}")
        sys.exit(0)
    else:
        print(f"❌ VIOLATIONS FOUND | {filepath}")
        if sim:
            print(f"  SIMULATION ({len(sim)}): lines {[l for l,_ in sim]}")
        if mem:
            print(f"  MEMORY ({len(mem)}): lines {[l for l,_ in mem]}")
        if hb:
            print(f"  HEARTBEAT ({len(hb)}): lines {[l for l,_ in hb]}")
        if bro:
            print(f"  BROTHERHOOD ({len(bro)}): lines {[l for l,_ in bro]}")
        sys.exit(1)

if __name__ == '__main__':
    main()
