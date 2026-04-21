#!/usr/bin/env python3
"""
NANO FLAWLESS Quality Checker
Targets 99.8% quality score.
"""
import sys
import re
import ast
from pathlib import Path

class NanoFlawlessChecker:
    def __init__(self):
        self.defects = []
        self.total_checks = 0

    def check_syntax(self, content):
        """Check Python syntax correctness."""
        self.total_checks += 1
        try:
            ast.parse(content)
            return True
        except SyntaxError as e:
            self.defects.append(("CRITICAL", f"Syntax error: {e.msg} at line {e.lineno}"))
            return False

    def check_simulation(self, content):
        """LAW_1: No simulation markers."""
        self.total_checks += 1
        patterns = [r'\bmock\b', r'\bfake\b', r'\bsimulate\b', r'\bstub\b']
        for pat in patterns:
            for m in re.finditer(pat, content, re.IGNORECASE):
                line = content[:m.start()].count('\n') + 1
                self.defects.append(("CRITICAL", f"Simulation marker '{m.group()}' at line {line}"))

    def check_error_handling(self, content):
        """Check for bare except and missing error handling."""
        self.total_checks += 1
        if 'except:' in content or 'except Exception:' in content:
            self.defects.append(("HIGH", "Bare or too-broad exception handler found"))

    def check_secrets(self, content):
        """Check for hardcoded secrets."""
        self.total_checks += 1
        secret_patterns = [
            r'api[_-]?key\s*=\s*["\']\w+',
            r'password\s*=\s*["\']\w+',
            r'secret\s*=\s*["\']\w+',
            r'token\s*=\s*["\']\w+',
        ]
        for pat in secret_patterns:
            for m in re.finditer(pat, content, re.IGNORECASE):
                line = content[:m.start()].count('\n') + 1
                self.defects.append(("CRITICAL", f"Potential hardcoded secret at line {line}"))

    def check_imports(self, content):
        """Check for dangerous imports."""
        self.total_checks += 1
        dangerous = ['pickle', 'marshal', 'exec', 'eval', '__import__']
        for mod in dangerous:
            if f'import {mod}' in content or f'from {mod}' in content:
                self.defects.append(("HIGH", f"Dangerous import: {mod}"))

    def check_comments(self, content):
        """Check for command language in comments (BOUND Article II)."""
        self.total_checks += 1
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                if any(cmd in stripped.lower() for cmd in ['you must', 'i order', 'you shall']):
                    line_num = content[:content.index(line)].count('\n') + 1
                    self.defects.append(("MEDIUM", f"Command language in comment at line {line_num}"))

    def calculate_score(self):
        """Calculate NANO_FLAWLESS score."""
        if self.total_checks == 0:
            return 1.0

        weights = {"CRITICAL": 0.10, "HIGH": 0.05, "MEDIUM": 0.02, "LOW": 0.01}
        penalty = sum(weights.get(d[0], 0.01) for d in self.defects)
        return max(0.0, 1.0 - penalty)

    def scan_file(self, filepath):
        """Scan a single file."""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            return

        if filepath.suffix == '.py':
            self.check_syntax(content)

        self.check_simulation(content)
        self.check_error_handling(content)
        self.check_secrets(content)
        self.check_imports(content)
        self.check_comments(content)

def main():
    if len(sys.argv) < 2:
        target = Path.cwd()
    else:
        target = Path(sys.argv[1])

    print("🔬 NANO FLAWLESS AUDIT")
    print("=" * 60)
    print(f"Target: {target}")
    print(f"Target Score: ≥ 0.998 (99.8%)")
    print("")

    checker = NanoFlawlessChecker()

    if target.is_file():
        files = [target]
    else:
        files = list(target.rglob("*.py")) + list(target.rglob("*.js")) + \
                list(target.rglob("*.ts")) + list(target.rglob("*.md"))

    for filepath in files:
        checker.scan_file(filepath)

    score = checker.calculate_score()

    # Count by severity
    critical = sum(1 for d in checker.defects if d[0] == "CRITICAL")
    high = sum(1 for d in checker.defects if d[0] == "HIGH")
    medium = sum(1 for d in checker.defects if d[0] == "MEDIUM")
    low = sum(1 for d in checker.defects if d[0] == "LOW")

    print(f"Files scanned: {len(files)}")
    print(f"Checks run:    {checker.total_checks}")
    print(f"Defects:       {len(checker.defects)}")
    print(f"  Critical: {critical} | High: {high} | Medium: {medium} | Low: {low}")
    print("")

    if checker.defects:
        print("Top Defects:")
        for sev, msg in checker.defects[:10]:
            icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚪")
            print(f"  {icon} [{sev}] {msg}")
        if len(checker.defects) > 10:
            print(f"  ... and {len(checker.defects) - 10} more")
        print("")

    if score >= 0.998:
        status = "✅ NANO_FLAWLESS"
    elif score >= 0.990:
        status = "⚠️  NEAR FLAWLESS"
    else:
        status = "❌ NEEDS WORK"

    print(f"Score: {score:.4f} ({score*100:.2f}%)")
    print(f"Status: {status}")

    sys.exit(0 if score >= 0.998 else 1)

if __name__ == '__main__':
    main()
