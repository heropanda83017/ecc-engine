#!/usr/bin/env python3
"""
quality_gate.py — Unified pre-merge quality check for AI-Investor profile.

Usage:
    python3 quality_gate.py [target_dir]

Checks:
  1. Run agentshield_check_all.py on target dir (or all .py in target)
  2. python3 -m py_compile on each .py file
  3. Check coding-patterns.yaml for patterns with confidence > 0.9
     that aren't referenced in the codebase
  4. Check session_metrics.csv for fail_rate > 30% in last 10 sessions
  5. Run hypothesis property-based tests (timeout 30s, xdist parallel)

Exit codes:
  0 = PASS (all checks passed)
  1 = FLAG (warnings only)
  2 = FAIL (blocking failures)
"""

import csv
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ── Hermes tools (optional — fall back to stdlib outside Hermes runtime) ──
try:
    from hermes_tools import terminal, write_file
except ImportError:
    # Fallback: define no-op stubs for standalone use
    def terminal(*args, **kwargs):
        raise RuntimeError("terminal() requires Hermes runtime")
    def write_file(*args, **kwargs):
        raise RuntimeError("write_file() requires Hermes runtime")


# ── Paths ──
BASE_DIR = Path(os.environ.get(
    'HERMES_PROFILE_DIR',
    Path.home() / '.hermes' / 'profiles' / 'ai-investor'
))
SCRIPTS_DIR = BASE_DIR / 'scripts'
DATA_DIR = BASE_DIR / 'data'
INSTINCTS_DIR = BASE_DIR / 'instincts'
CODING_PATTERNS_FILE = INSTINCTS_DIR / 'coding-patterns.yaml'
METRICS_FILE = DATA_DIR / 'session_metrics.csv'
AGENTSHIELD_CHECK = SCRIPTS_DIR / 'agentshield_check.py'
AGENTSHIELD_CHECK_ALL = SCRIPTS_DIR / 'agentshield_check_all.py'


# ── ANSI helpers ──
GREEN_CHECK = "\u2705"
YELLOW_WARN = "\u26a0\ufe0f"
RED_CROSS = "\u274c"
BOLD = "\033[1m"
RESET = "\033[0m"


# ── Check 1: Agentshield ──

def run_agentshield(target_dir: Path) -> tuple[bool, str, int]:
    """Run agentshield_check_all.py on target directory."""
    if not AGENTSHIELD_CHECK_ALL.exists():
        return False, f"agentshield_check_all.py not found at {AGENTSHIELD_CHECK_ALL}", 2

    result = subprocess.run(
        [sys.executable, str(AGENTSHIELD_CHECK_ALL), str(target_dir)],
        capture_output=True, text=True
    )

    # Parse the violation count from output summary
    total_violations = 0
    for line in result.stdout.splitlines():
        m = re.search(r'Total violations:\s+(\d+)', line)
        if m:
            total_violations = int(m.group(1))
            break
        # Also check old format
        m = re.search(r'With violations:\s+(\d+)', line)
        if m:
            # Old format: count of files with violations
            files_with_violations = int(m.group(1))
            if files_with_violations > 0:
                total_violations = files_with_violations

    if result.returncode == 0 and total_violations == 0:
        return True, f"0 violations", 0
    else:
        return False, f"{total_violations} violations found", 1


# ── Check 2: PyCompile ──

def run_py_compile(target_dir: Path) -> tuple[bool, str, int]:
    """Run python3 -m py_compile on each .py file."""
    py_files = sorted(target_dir.glob("*.py"))
    if not py_files:
        return True, "No .py files to check", 0

    passed = 0
    failed = 0
    errors = []

    for py_file in py_files:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(py_file)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            passed += 1
        else:
            failed += 1
            stderr = result.stderr.strip()
            errors.append(f"  {py_file.name}: {stderr[:120]}")

    total = len(py_files)
    if failed == 0:
        return True, f"{passed}/{total} files pass", 0
    else:
        detail = f"{passed}/{total} files pass ({failed} failed)"
        if errors:
            detail += "\n" + "\n".join(errors[:5])
        return False, detail, 2


# ── Check 3: Coding patterns ──

def check_coding_patterns(target_dir: Path) -> tuple[bool, str, int]:
    """Check coding-patterns.yaml for patterns with confidence > 0.9
    that aren't referenced in the codebase."""
    if not CODING_PATTERNS_FILE.exists():
        return True, "No coding-patterns.yaml found", 0

    # Parse YAML manually to avoid dependency
    content = CODING_PATTERNS_FILE.read_text(encoding='utf-8')

    # Find all patterns with their confidence and name
    # Simple line-by-line parser for the YAML structure
    patterns = []
    current_pattern = {}

    for line in content.splitlines():
        pattern_match = re.match(r'^-\s+pattern:\s+(\S+)', line)
        if pattern_match:
            if current_pattern and 'name' in current_pattern and 'confidence' in current_pattern:
                patterns.append(current_pattern)
            current_pattern = {'name': pattern_match.group(1)}
            continue

        conf_match = re.match(r'\s+confidence:\s+([\d.]+)', line)
        if conf_match and current_pattern:
            current_pattern['confidence'] = float(conf_match.group(1))
            continue

        desc_match = re.match(r'\s+description:\s+(.+)', line)
        if desc_match and current_pattern:
            current_pattern['description'] = desc_match.group(1).strip("'\"")

    # Append last pattern
    if current_pattern and 'name' in current_pattern and 'confidence' in current_pattern:
        patterns.append(current_pattern)

    # Filter high-confidence patterns
    high_conf_patterns = [p for p in patterns if p.get('confidence', 0) > 0.9]

    if not high_conf_patterns:
        return True, "No high-confidence patterns (>0.9) to verify", 0

    # Scan the codebase for references to each pattern
    unverified = []
    for pattern in high_conf_patterns:
        pattern_name = pattern['name']
        # Search in Python files under target_dir
        found = False
        for py_file in target_dir.rglob("*.py"):
            py_content = py_file.read_text(encoding='utf-8')
            # Look for the pattern name as a function name, variable, or docstring
            if pattern_name in py_content:
                found = True
                break

        if not found:
            unverified.append(pattern)

    if not unverified:
        return True, "All high-confidence patterns verified in codebase", 0
    else:
        names = ", ".join(p['name'] for p in unverified)
        return False, f"{len(unverified)} high-confidence patterns not verified recently: {names}", 1


# ── Check 4: Session metrics ──

def check_session_metrics() -> tuple[bool, str, int]:
    """Check session_metrics.csv for fail_rate > 30% in last 10 sessions."""
    if not METRICS_FILE.exists():
        return True, "No session_metrics.csv found", 0

    rows = []
    with open(METRICS_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return True, "No session metrics recorded", 0

    # Take last 10 sessions (most recent first based on file order)
    last_10 = rows[-10:]

    if not last_10:
        return True, "No session data available", 0

    total = len(last_10)
    fail_count = sum(1 for r in last_10 if r.get('verdict', '').strip().lower() == 'fail')
    fail_rate = (fail_count / total) * 100 if total > 0 else 0

    if fail_rate > 30:
        return False, f"fail rate {fail_rate:.0f}% in last {total} sessions ({fail_count}/{total})", 2
    else:
        return True, f"fail rate {fail_rate:.0f}% in last {total} sessions ({fail_count}/{total})", 0


# ── Check 5: Hypothesis property-based tests ──

def check_hypothesis_tests(target_dir: Path) -> tuple[bool, str, int]:
    """Run hypothesis property-based tests with 30s timeout and xdist."""
    test_dir = target_dir / "tests"
    if not test_dir.is_dir():
        return True, "No tests/ directory found", 0

    hypothesis_files = []
    for tf in sorted(test_dir.glob("test_*.py")):
        content = tf.read_text(encoding="utf-8", errors="replace")
        if "@given" in content and "hypothesis" in content:
            hypothesis_files.append(tf)

    if not hypothesis_files:
        return True, "No hypothesis tests found", 0

    passed = 0
    failed = 0
    timed_out = 0
    errors = []

    for hf in hypothesis_files:
        cmd = [
            "timeout", "30",
            sys.executable, "-m", "pytest",
            str(hf), "-n", "auto", "-q", "--tb=short"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if result.returncode == 0:
            passed += 1
        elif result.returncode == 124:  # timeout exit code
            timed_out += 1
            errors.append(f"  {hf.name}: TIMEOUT (>30s)")
        else:
            failed += 1
            errors.append(f"  {hf.name}: FAILED")

    total = len(hypothesis_files)
    detail = f"{passed}/{total} hypothesis test files pass"
    if failed > 0:
        detail += f" ({failed} failed)"
    if timed_out > 0:
        detail += f" ({timed_out} timed out)"

    if failed > 0:
        return False, detail + "\n" + "\n".join(errors), 1
    elif timed_out > 0:
        return False, detail + "\n" + "\n".join(errors), 2
    else:
        return True, detail, 0


# ── Check 6: CodeGraph index freshness ──

def check_codegraph_sync(target_dir: Path) -> tuple[bool, str, int]:
    """Check if CodeGraph index exists and optionally sync."""
    codegraph_dir = target_dir / ".codegraph"
    if not codegraph_dir.exists():
        return True, "No CodeGraph index (run 'codegraph init')", 0

    db_file = codegraph_dir / "codegraph.db"
    if not db_file.exists():
        return True, "CodeGraph index incomplete (no db)", 0

    # Check index freshness (compare db mtime vs newest .py mtime)
    db_mtime = db_file.stat().st_mtime
    newest_py = 0
    for py_file in target_dir.rglob("*.py"):
        mtime = py_file.stat().st_mtime
        if mtime > newest_py:
            newest_py = mtime

    if newest_py > db_mtime + 60:  # 60s grace period
        return False, f"Index stale (newer .py files exist, run 'codegraph sync')", 1
    return True, f"Index fresh ({target_dir.name})", 0


# ── Main ──

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Unified pre-merge quality check for AI-Investor profile',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 quality_gate.py
  python3 quality_gate.py /path/to/target
  python3 quality_gate.py --help
        """
    )
    parser.add_argument(
        'target_dir',
        nargs='?',
        default=str(SCRIPTS_DIR),
        help='Target directory to scan (default: scripts directory)'
    )

    args = parser.parse_args()
    target_dir = Path(args.target_dir).resolve()

    if not target_dir.is_dir():
        print(f"ERROR: Not a directory: {target_dir}", file=sys.stderr)
        sys.exit(2)

    today_str = date.today().strftime('%Y-%m-%d')

    # ── Header ──
    print(f"=== QUALITY GATE ===")
    print(f"Target: {target_dir}")
    print(f"Date: {today_str}")
    print()

    # ── Checks ──
    checks = []

    # Check 1: Agentshield
    shield_ok, shield_msg, shield_code = run_agentshield(target_dir)
    checks.append((shield_ok, shield_msg, shield_code))
    symbol = GREEN_CHECK if shield_ok else RED_CROSS
    print(f"{symbol} Agentshield: {shield_msg}")

    # Check 2: PyCompile
    compile_ok, compile_msg, compile_code = run_py_compile(target_dir)
    checks.append((compile_ok, compile_msg, compile_code))
    symbol = GREEN_CHECK if compile_ok else RED_CROSS
    print(f"{symbol} PyCompile: {compile_msg}")

    # Check 3: Coding patterns
    patterns_ok, patterns_msg, patterns_code = check_coding_patterns(target_dir)
    checks.append((patterns_ok, patterns_msg, patterns_code))
    symbol = GREEN_CHECK if patterns_ok else YELLOW_WARN
    print(f"{symbol} Patterns: {patterns_msg}")

    # Check 4: Session metrics
    metrics_ok, metrics_msg, metrics_code = check_session_metrics()
    checks.append((metrics_ok, metrics_msg, metrics_code))
    symbol = GREEN_CHECK if metrics_ok else RED_CROSS
    print(f"{symbol} Metrics: {metrics_msg}")

    # Check 5: Hypothesis tests
    hypo_ok, hypo_msg, hypo_code = check_hypothesis_tests(target_dir)
    checks.append((hypo_ok, hypo_msg, hypo_code))
    if hypo_code == 2:
        symbol = RED_CROSS
    elif hypo_code == 1:
        symbol = YELLOW_WARN
    else:
        symbol = GREEN_CHECK
    print(f"{symbol} Hypothesis: {hypo_msg}")

    # Check 6: CodeGraph index freshness
    cg_ok, cg_msg, cg_code = check_codegraph_sync(target_dir)
    checks.append((cg_ok, cg_msg, cg_code))
    symbol = GREEN_CHECK if cg_ok else YELLOW_WARN
    print(f"{symbol} CodeGraph: {cg_msg}")

    # ── Verdict ──
    print()
    failures = sum(1 for ok, _, code in checks if not ok and code == 2)
    warnings = sum(1 for ok, _, code in checks if not ok and code == 1)
    passes = sum(1 for ok, _, _ in checks if ok)

    if failures > 0:
        verdict = "FAIL"
        exit_code = 2
        parts = []
        if failures > 0:
            parts.append(f"{failures} failure{'s' if failures > 1 else ''}")
        if warnings > 0:
            parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
        print(f"VERDICT: {verdict} ({', '.join(parts)})")
    elif warnings > 0:
        verdict = "FLAG"
        exit_code = 1
        parts = []
        if warnings > 0:
            parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
        print(f"VERDICT: {verdict} ({', '.join(parts)})")
    else:
        verdict = "PASS"
        exit_code = 0
        print(f"VERDICT: {verdict}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
