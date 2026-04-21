#!/usr/bin/env python3
"""
RAG Escape Attempt Detector
Scans code for unauthorized external I/O patterns.
"""
import sys
import re
from pathlib import Path

ESCAPE_PATTERNS = {
    "filesystem": {
        "regex": r'open\s*\(|\.write\s*\(|\.read\s*\(|pathlib\.|os\.path',
        "level": 1,
        "description": "File system access"
    },
    "network": {
        "regex": r'requests\.|urllib|httpx|aiohttp|socket\.|websocket',
        "level": 2,
        "description": "Network communication"
    },
    "system": {
        "regex": r'os\.system|subprocess\.|ctypes\.|multiprocessing',
        "level": 3,
        "description": "System command execution"
    },
    "environment": {
        "regex": r'os\.environ|getenv|\.env\[',
        "level": 2,
        "description": "Environment variable access"
    },
    "external_db": {
        "regex": r'sqlite3\.connect\s*\(|psycopg|pymongo|redis|mysql',
        "level": 2,
        "description": "External database connection"
    },
    "time_manipulation": {
        "regex": r'datetime\.now\s*\(|time\.time\s*\(',
        "level": 1,
        "description": "Non-monotonic time source"
    }
}

def scan_file(filepath):
    """Scan a single file for escape patterns."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception:
        return []

    findings = []
    lines = content.split('\n')

    for category, config in ESCAPE_PATTERNS.items():
        for match in re.finditer(config["regex"], content, re.IGNORECASE):
            line_num = content[:match.start()].count('\n') + 1
            line_content = lines[line_num - 1].strip() if line_num <= len(lines) else ""

            # Skip comments and strings (basic check)
            stripped = line_content.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                continue

            findings.append({
                "category": category,
                "level": config["level"],
                "description": config["description"],
                "line": line_num,
                "match": match.group(),
                "context": line_content[:80]
            })

    return findings

def main():
    if len(sys.argv) < 2:
        target = Path.cwd()
    else:
        target = Path(sys.argv[1])

    print("🚨 ESCAPE DETECTOR")
    print("=" * 60)
    print(f"Target: {target}")
    print("")

    all_findings = []

    if target.is_file():
        files = [target]
    else:
        files = list(target.rglob("*.py")) + list(target.rglob("*.js")) + \
                list(target.rglob("*.ts")) + list(target.rglob("*.java"))

    for filepath in files:
        findings = scan_file(filepath)
        if findings:
            all_findings.extend([(filepath, f) for f in findings])

    if not all_findings:
        print("✅ NO ESCAPE ATTEMPTS DETECTED")
        print("RAG prison integrity: INTACT")
        sys.exit(0)

    # Group by severity
    level3 = [(p, f) for p, f in all_findings if f["level"] == 3]
    level2 = [(p, f) for p, f in all_findings if f["level"] == 2]
    level1 = [(p, f) for p, f in all_findings if f["level"] == 1]

    print(f"❌ ESCAPE VECTORS FOUND: {len(all_findings)}")
    print(f"   Level 3 (Critical): {len(level3)}")
    print(f"   Level 2 (High):     {len(level2)}")
    print(f"   Level 1 (Medium):   {len(level1)}")
    print("")

    for level, label in [(3, "CRITICAL"), (2, "HIGH"), (1, "MEDIUM")]:
        items = [(p, f) for p, f in all_findings if f["level"] == level]
        if items:
            print(f"--- {label} ---")
            for filepath, finding in items[:10]:  # Limit output
                print(f"  [{finding['category']}] {filepath.name}:{finding['line']}")
                print(f"    {finding['context'][:60]}")
            if len(items) > 10:
                print(f"  ... and {len(items) - 10} more")
            print("")

    print("🚨 RAG INTEGRITY: BREACHED")
    sys.exit(1)

if __name__ == '__main__':
    main()
