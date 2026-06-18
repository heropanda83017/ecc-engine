#!/usr/bin/env python3
"""
agentshield_check.py — Pre-merge safety checks enforcing 21 coding patterns.

Usage:
    python3 agentshield_check.py <filepath>

Checks:
  Existing (4):
  - atomic-write: .write_text() calls NOT followed by tmp file pattern
  - is-not-none: `if X and` where X is a numeric variable
  - signal-timeout: external calls (requests.get, bs.login) without timeout
  - re-read-before-patch: patch() calls preceded by write_file() in same function

  Original (11):
  - secret-detection: Hardcoded API_KEY/TOKEN/SECRET/PASSWORD assignments
  - path-safety: Absolute paths not using Path(__file__).parent
  - encoding-safety: .read_text()/.write_text()/.open() without encoding=
  - exception-granularity: Bare `except:` without Exception type
  - shell-injection: terminal() calls containing f-strings with variables
  - nesting-depth: Blocks with >5 levels of indentation
  - file-overwrite: write_file() to existing path without pre-check
  - line-length: Lines > 120 characters
  - trailing-whitespace: Trailing whitespace on non-empty lines
  - todo-left: TODO/FIXME/HACK/XXX comments not in docstrings
  - import-safety: Imports of requests, httpx, yaml, dotenv not in known-good list

  AI Slop Detection (5 new — 2026-06-17):
  - hallucinated-import: AI-fabricated import patterns (langchain.vectorstores.Chroma, etc.)
  - dead-code-block: Functions/classes defined but never called
  - over-commented: Comment-to-code ratio > 40%
  - fake-todo: Vague TODO/FIXME placeholders ("TODO: fix later")
  - hallucinated-api: Placeholder API endpoints (api.example.com, localhost:/api/v1/)

Returns exit code 0 if clean, 1 if violations found.
"""

import ast
import hashlib
import os
import re
import sys
from pathlib import Path
from ecc import config


# ── Known-good import sources (projects that legitimately use these libs) ──
# 增量检查缓存
_CHECKSUM_CACHE = {}

def _file_checksum(path: str) -> str:
    """计算文件 SHA256，用于增量检查"""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""

def _load_checksum_cache(cache_path: str) -> dict:
    """加载增量缓存"""
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def _save_checksum_cache(cache_path: str, cache: dict):
    """保存增量缓存"""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path + '.tmp', 'w') as f:
        json.dump(cache, f)
    os.replace(cache_path + '.tmp', cache_path)


KNOWN_GOOD_IMPORTS = {
    "hermes_tools",
    "hermes.skills",
    "hermes.plugins",
}


class Violation:
    """A single coding pattern violation."""

    def __init__(self, check_name: str, line: int, code: str, message: str):
        self.check_name = check_name
        self.line = line
        self.code = code.strip()
        self.message = message

    def __str__(self) -> str:
        return (
            f"  [{self.check_name}] Line {self.line}: {self.message}\n"
            f"    → {self.code}"
        )


# ══════════════════════════════════════════════════════════════════════
# Existing check functions (4)
# ══════════════════════════════════════════════════════════════════════


def check_atomic_write(lines: list, filepath: str) -> list:
    """
    Pattern: atomic-write
    Avoid direct .write_text() calls not followed by tmp-file pattern.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        if ".write_text(" in line:
            surrounding = lines[max(0, i - 5):i + 3]
            has_tmp = any(
                ".tmp" in sl or "os.replace" in sl or "tempfile" in sl
                for sl in surrounding
            )
            if not has_tmp:
                violations.append(Violation(
                    check_name="atomic-write",
                    line=i,
                    code=line,
                    message="Direct .write_text() call without tmp-file + os.replace pattern"
                ))
    return violations


def check_is_not_none(lines: list, filepath: str) -> list:
    """
    Pattern: is-not-none
    Avoid `if X and` where X might be a numeric variable (falsy on 0.0).
    """
    violations = []
    numeric_pattern = re.compile(
        r'\b(weight|score|value|rate|ratio|amount|count|total|price|factor|'
        r'alpha|beta|threshold|limit|size|num|idx|index|offset)\b',
        re.IGNORECASE
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        for prefix in ("if ", "elif "):
            if stripped.startswith(prefix):
                rest = stripped[len(prefix):]
                op_match = re.search(r'\band\b', rest)
                if op_match:
                    lhs = rest[:op_match.start()].strip()
                    lhs = lhs.strip("() ")
                    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', lhs):
                        if numeric_pattern.search(lhs):
                            after_and = rest[op_match.end():].strip()
                            if 'is not None' not in after_and:
                                violations.append(Violation(
                                    check_name="is-not-none",
                                    line=i,
                                    code=line,
                                    message=f"'{lhs}' is a numeric variable — use "
                                    f"'{lhs} is not None' instead of relying on truthiness"
                                ))
    return violations


def check_signal_timeout(lines: list, filepath: str) -> list:
    """
    Pattern: signal-timeout
    External resource calls must have a timeout argument.
    """
    violations = []
    external_patterns = [
        (r'\breq(?:uests)?\.(?:get|post|put|delete|patch|head|options)\s*\(',
         "requests.*() call without timeout"),
        (r'\bbs\.\w+\s*\(', "baostock (bs.*) call without timeout"),
        (r'\burlopen\s*\(', "urllib urlopen() without timeout"),
        (r'\bhttpx\.(?:get|post|put|delete|patch|head|options|stream|Client)\s*\(',
         "httpx.*() call without timeout"),
        (r'\bsession\.(?:get|post|put|delete|patch|head|options)\s*\(',
         "aiohttp session.*() call without timeout"),
    ]

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        for pattern, desc in external_patterns:
            if re.search(pattern, stripped):
                block = stripped
                j = i
                while block.count('(') > block.count(')') and j < len(lines):
                    j += 1
                    block += " " + lines[j - 1].strip()
                if 'timeout' not in block:
                    violations.append(Violation(
                        check_name="signal-timeout",
                        line=i,
                        code=line.strip(),
                        message=f"{desc} — must include timeout= keyword argument"
                    ))
    return violations


def check_re_read_before_patch(lines: list, filepath: str) -> list:
    """
    Pattern: re-read-before-patch
    patch() calls preceded by write_file() in the same function risk stale context.
    """
    violations = []
    current_func = None
    has_write_file = False
    write_file_line = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        func_def = re.match(r'^def\s+(\w+)\s*\(', stripped)
        if func_def:
            current_func = func_def.group(1)
            has_write_file = False
            write_file_line = 0
            continue

        if current_func and 'write_file(' in stripped and not stripped.startswith('#'):
            has_write_file = True
            write_file_line = i

        if current_func and has_write_file:
            if 'patch(' in stripped and not stripped.startswith('#'):
                violations.append(Violation(
                    check_name="re-read-before-patch",
                    line=i,
                    code=stripped,
                    message=f"patch() at line {i} after write_file() at line "
                    f"{write_file_line} in function '{current_func}' — "
                    f"re-read the file before patching to avoid stale context"
                ))
    return violations


# ══════════════════════════════════════════════════════════════════════
# New check functions (11)
# ══════════════════════════════════════════════════════════════════════


def check_secret_detection(lines: list, filepath: str) -> list:
    """
    Scan for hardcoded API_KEY, TOKEN, SECRET, PASSWORD strings in assignments.
    """
    violations = []
    secret_pattern = re.compile(
        r'(api_key|token|secret|password)\s*=\s*["\'][^"\'\s]+["\']',
        re.IGNORECASE
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Skip imports (from x import y)
        if stripped.startswith('import') or stripped.startswith('from'):
            continue
        if secret_pattern.search(stripped):
            violations.append(Violation(
                check_name="secret-detection",
                line=i,
                code=stripped,
                message="Hardcoded credential detected — use environment variables or .env instead"
            ))
    return violations


def check_path_safety(lines: list, filepath: str) -> list:
    """
    Scan for absolute paths like /mnt/ or C:/ that aren't using Path(__file__).parent patterns.
    """
    violations = []
    # Patterns for absolute path strings
    path_pattern = re.compile(
        r'["\']((?:/mnt/[a-zA-Z]/|/home/|[A-Za-z]:\\|/[a-z]{2,}/[a-zA-Z]))',
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if path_pattern.search(stripped):
            # Check if the line uses Path(__file__).parent or similar dynamic patterns
            if 'Path(__file__).parent' not in stripped and \
               'Path(__file__).resolve().parent' not in stripped and \
               'Path.home()' not in stripped and \
               'os.environ.get' not in stripped and \
               'os.getenv' not in stripped:
                violations.append(Violation(
                    check_name="path-safety",
                    line=i,
                    code=stripped,
                    message="Hardcoded absolute path detected — use Path(__file__).parent or Path.home() instead"
                ))
    return violations


def check_encoding_safety(lines: list, filepath: str) -> list:
    """
    Scan for .read_text()/.write_text()/.open() calls without encoding= parameter.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Check .read_text() and .write_text()
        if '.read_text(' in stripped or '.write_text(' in stripped:
            if 'encoding=' not in stripped:
                violations.append(Violation(
                    check_name="encoding-safety",
                    line=i,
                    code=stripped,
                    message=".read_text()/.write_text() call without encoding= parameter"
                ))
        # Check open() calls
        if 'open(' in stripped and not stripped.startswith('import') and not stripped.startswith('from'):
            # Make sure we're not in a docstring context
            if 'encoding=' not in stripped:
                violations.append(Violation(
                    check_name="encoding-safety",
                    line=i,
                    code=stripped,
                    message="open() call without encoding= parameter"
                ))
    return violations


def check_exception_granularity(lines: list, filepath: str) -> list:
    """
    Scan for bare `except:` not followed by Exception type.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Match bare 'except:' — not 'except Exception:' or 'except ValueError:'
        if re.match(r'^except\s*:', stripped):
            violations.append(Violation(
                check_name="exception-granularity",
                line=i,
                code=stripped,
                message="Bare 'except:' clause — specify exception type (Exception, ValueError, etc.)"
            ))
    return violations


def check_shell_injection(lines: list, filepath: str) -> list:
    """
    Scan for terminal() calls containing f-strings with variables.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Look for terminal( call containing an f-string
        if 'terminal(' in stripped and "f'" in stripped or 'terminal(' in stripped and 'f"' in stripped:
            # Check if the f-string has format variables
            if re.search(r'f["\'][^"\']*\{[^}]+\}', stripped):
                violations.append(Violation(
                    check_name="shell-injection",
                    line=i,
                    code=stripped,
                    message="terminal() call with f-string containing variables — potential shell injection risk"
                ))
    return violations


def check_nesting_depth(lines: list, filepath: str) -> list:
    """
    Scan for blocks with >5 levels of indentation (12 spaces / 3 tabs).
    """
    violations = []
    nesting_violations = set()
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        if not stripped or stripped.strip() == '':
            continue
        # Count leading whitespace
        leading = line[:len(line) - len(line.lstrip())]
        # Count indentation levels (assumes 4-space indent or tab)
        if '\t' in leading:
            depth = len(leading.replace('\t', '    ')) // 4
        else:
            depth = len(leading) // 4

        # Skip the line if it's just closing braces/parentheses
        if stripped.strip() in ('}', ']', ')'):
            continue

        if depth > 5 and i not in nesting_violations:
            nesting_violations.add(i)
            # Find the actual code (not blank/comment continuation)
            if '#' in stripped:
                code_part = stripped.split('#')[0].strip()
            else:
                code_part = stripped.strip()
            if code_part:
                violations.append(Violation(
                    check_name="nesting-depth",
                    line=i,
                    code=stripped,
                    message=f"Excessive nesting ({depth} levels deep, max 5) — refactor into helper functions"
                ))
    return violations


def check_file_overwrite(lines: list, filepath: str) -> list:
    """
    Scan for write_file() calls where target path exists on same line without check.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Look for write_file( calls
        if 'write_file(' in stripped:
            # Check if this line has any existence check pattern
            has_check = False
            # Look at surrounding lines for existence checks
            start = max(0, i - 4)
            end = min(len(lines), i)
            for ctx_line in lines[start:end]:
                ctx_stripped = ctx_line.strip()
                if 'exists' in ctx_stripped or 'is_file' in ctx_stripped or 'is_dir' in ctx_stripped:
                    has_check = True
                    break
            if not has_check:
                violations.append(Violation(
                    check_name="file-overwrite",
                    line=i,
                    code=stripped,
                    message="write_file() call without prior existence check — may accidentally overwrite data"
                ))
    return violations


def check_line_length(lines: list, filepath: str) -> list:
    """
    Scan for lines > 120 characters.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        # Skip very long comment/docstring blocks that are known patterns
        if len(line) > 120:
            violations.append(Violation(
                check_name="line-length",
                line=i,
                code=line.rstrip('\n'),
                message=f"Line exceeds 120 characters ({len(line.rstrip())} chars)"
            ))
    return violations


def check_trailing_whitespace(lines: list, filepath: str) -> list:
    """
    Scan for trailing whitespace on non-empty lines.
    """
    violations = []
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip('\n')
        if stripped and stripped != stripped.rstrip():
            violations.append(Violation(
                check_name="trailing-whitespace",
                line=i,
                code=stripped.rstrip() + '·' * (len(stripped) - len(stripped.rstrip())),
                message="Trailing whitespace detected"
            ))
    return violations


def check_todo_left(lines: list, filepath: str) -> list:
    """
    Scan for TODO/FIXME/HACK/XXX comments not in docstrings.
    """
    violations = []
    in_docstring = False
    docstring_delimiters = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track docstring boundaries
        if stripped.startswith('"""') or stripped.startswith("'''"):
            docstring_delimiters += stripped.count('"""') if '"""' in stripped else stripped.count("'''")
            if docstring_delimiters % 2 == 1:
                in_docstring = True
            else:
                in_docstring = False
            continue

        if in_docstring:
            continue

        # Check for TODO/FIXME/HACK/XXX in comments
        comment_match = re.search(r'#.*\b(TODO|FIXME|HACK|XXX)\b', stripped)
        if comment_match:
            violations.append(Violation(
                check_name="todo-left",
                line=i,
                code=stripped,
                message=f"{comment_match.group(1)} comment left in code — resolve before merging"
            ))
    return violations


def check_import_safety(lines: list, filepath: str) -> list:
    """
    Scan for imports of: requests, httpx, yaml, dotenv that aren't in known-good list.
    """
    violations = []
    dangerous_imports = {'requests', 'httpx', 'yaml', 'dotenv'}

    filepath_str = str(filepath)
    # If file is part of known-good module, skip
    if any(good in filepath_str for good in KNOWN_GOOD_IMPORTS):
        return violations

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue

        # Match: import requests  or  from requests import ...
        m = re.match(r'^(?:import|from)\s+(\S+)', stripped)
        if m:
            module_name = m.group(1).split('.')[0]
            if module_name in dangerous_imports:
                violations.append(Violation(
                    check_name="import-safety",
                    line=i,
                    code=stripped,
                    message=f"Import of '{module_name}' detected — not in known-good list; "
                    f"approve before using"
                ))
    return violations


# ══════════════════════════════════════════════════════════════════════
# AI Slop detection checks (C11-C15) — 2026-06-17
# ══════════════════════════════════════════════════════════════════════


def check_hallucinated_import(lines: list, filepath: str) -> list:
    """
    C11: Detect hallucinated imports — modules that AI models often fabricate.
    Checks import statements against a known-good set of available modules.
    """
    violations = []
    # Known false-positive modules (too generic to check)
    HALLUCINATION_PATTERNS = re.compile(
        r'(import|from)\s+('
        r'langchain\.vectorstores\.Chroma|'
        r'langchain\.embeddings\.OpenAIEmbeddings|'
        r'transformers\.pipelines|'
        r'spacy\.load|'
        r'nltk\.download|'
        r'utils\.common|'
        r'utils\.helpers|'
        r'config\.settings|'
        r'models\.user|'
        r'models\.database'
        r')',
        re.IGNORECASE
    )
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"') or stripped.startswith("'"):
            continue
        if HALLUCINATION_PATTERNS.search(stripped):
            violations.append(Violation(
                check_name="hallucinated-import",
                line=i,
                code=stripped,
                message="Possible hallucinated import pattern — verify this module actually exists in the project's dependencies"
            ))
    return violations


def check_dead_code_block(lines: list, filepath: str) -> list:
    """
    C12: Detect dead code blocks — functions/classes defined but never called outside __main__.
    Uses AST to identify definitions without references.
    """
    violations = []
    try:
        source = '\n'.join(lines)
        tree = ast.parse(source)

        # Collect all defined names at module level
        defined = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)

        # Collect all referenced names
        referenced = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    referenced.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    referenced.add(node.func.attr)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)

        # Exclude special names and __main__ guarded names
        excluded = {'setup', 'teardown', 'setUp', 'tearDown',
                    'main', 'run', 'app', 'router', 'handler'}
        defined = defined - excluded

        # Dead code = defined but never referenced outside own definition
        dead = defined - referenced
        if dead:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in dead:
                        # Check if it's defined inside __main__ guard
                        for parent in ast.walk(tree):
                            if isinstance(parent, ast.If):
                                guard = parent.test
                                if (isinstance(guard, ast.Compare) and
                                        isinstance(guard.left, ast.Name) and
                                        guard.left.id == '__name__'):
                                    if (node.lineno >= guard.lineno and
                                            (node.end_lineno or 0) <= (getattr(parent, 'end_lineno', None) or 0)):
                                        dead.discard(node.name)
                                        break

            # Recheck after __main__ filtering
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in dead:
                        violations.append(Violation(
                            check_name="dead-code-block",
                            line=node.lineno,
                            code=f"def {node.name}(...) / class {node.name}",
                            message=f"Defined '{node.name}' but never called — possible AI-generated dead code"
                        ))
    except SyntaxError:
        pass  # Skip files with syntax errors
    return violations


def check_over_commented(lines: list, filepath: str) -> list:
    """
    C13: Detect over-commented code — comment lines > 40% of total non-blank lines.
    AI-generated code often has excessive inline comments.
    """
    violations = []
    code_lines = 0
    comment_lines = 0

    in_docstring = False
    docstring_delimiters = 0

    for line in lines:
        stripped = line.strip()

        # Track docstrings
        if stripped.startswith('"""') or stripped.startswith("'''"):
            docstring_delimiters += stripped.count('"""') if '"""' in stripped else stripped.count("'''")
            if docstring_delimiters % 2 == 1:
                in_docstring = True
            else:
                in_docstring = False
            comment_lines += 1
            continue

        if in_docstring:
            comment_lines += 1
            continue

        if not stripped:
            continue

        if stripped.startswith('#'):
            comment_lines += 1
        else:
            code_lines += 1

    total_non_blank = code_lines + comment_lines
    if total_non_blank > 0:
        ratio = comment_lines / total_non_blank
        if ratio > 0.40:
            violations.append(Violation(
                check_name="over-commented",
                line=1,
                code=f"comment ratio: {ratio:.0%} ({comment_lines}/{total_non_blank} lines)",
                message=f"Comment-to-code ratio {ratio:.0%} exceeds 40% — AI-generated code often over-comments. "
                        f"Consider removing inline commentary that restates obvious logic."
            ))
    return violations


def check_fake_todo(lines: list, filepath: str) -> list:
    """
    C14: Detect fake/vague TODO comments — TODO/FIXME with meaningless descriptions.
    AI often generates placeholder TODOs like "TODO: implement later".
    """
    violations = []
    VAGUE_TODO = re.compile(
        r'#\s*(TODO|FIXME|HACK|XXX)\s*[:.-]?\s*'
        r'(later|soon|someday|eventually|sometime|maybe|perhaps|'
        r'fix\s*(me|this|it)|implement|add|remove|check|update|'
        r'refactor|clean|improve|optimize|revisit|consider'
        r')(?:\s|$|\.|,|!|\?)',
        re.IGNORECASE
    )

    in_docstring = False
    docstring_delimiters = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track docstring boundaries
        if stripped.startswith('"""') or stripped.startswith("'''"):
            docstring_delimiters += stripped.count('"""') if '"""' in stripped else stripped.count("'''")
            if docstring_delimiters % 2 == 1:
                in_docstring = True
            else:
                in_docstring = False
            continue

        if in_docstring:
            continue

        if VAGUE_TODO.search(stripped):
            violations.append(Violation(
                check_name="fake-todo",
                line=i,
                code=stripped,
                message="Vague TODO/FIXME — AI-generated placeholder. Replace with specific action item "
                        "(e.g. 'TODO: add timeout retry in fetch_data()')."
            ))
    return violations


def check_hallucinated_api(lines: list, filepath: str) -> list:
    """
    C15: Detect hallucinated API endpoints — URLs/endpoints that look AI-generated.
    Checks for common patterns like placeholder domains, fake API paths.
    """
    violations = []
    HALLUCINATED_API = re.compile(
        r'(https?://(?:'
        r'api\.(?:example|test|demo|sample|placeholder|your-domain|my-service)\.com|'
        r'(?:example|test|demo)\.(?:com|org|net|io|api)|'
        r'localhost:\d+/api/v\d/.*'
        r'))',
        re.IGNORECASE
    )

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue

        m = HALLUCINATED_API.search(stripped)
        if m:
            violations.append(Violation(
                check_name="hallucinated-api",
                line=i,
                code=stripped,
                message=f"Possible hallucinated API endpoint: {m.group(1)} — "
                        f"verify this endpoint exists before using in production"
            ))
    return violations


# ══════════════════════════════════════════════════════════════════════
# Over-engineering detection (C16) — Ponytail inspired — 2026-06-17
# ══════════════════════════════════════════════════════════════════════


def check_over_engineering(lines: list, filepath: str) -> list:
    """
    C16: Detect over-engineering patterns — unnecessary abstractions,
    single-implementation interfaces, speculative flexibility.
    Inspired by Ponytail's 5-tag review format.
    """
    violations = []
    source = '\n'.join(lines)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return violations

    # Pattern 1: Interface/ABC with single implementation
    interfaces = set()
    implementations = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in ('ABC', 'Protocol', 'Interface'):
                    interfaces.add(node.name)
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    for deco in item.decorator_list:
                        if isinstance(deco, ast.Name) and deco.id == 'abstractmethod':
                            interfaces.add(node.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in interfaces:
                    implementations.add(node.name)

    single_impl = interfaces - implementations
    for name in single_impl:
        violations.append(Violation(
            check_name="over-engineering",
            line=1,
            code=f"class {name}(ABC/Protocol)",
            message=f"yagni: '{name}' is an interface/ABC with no implementations — speculative abstraction. Remove until a second implementation exists."
        ))

    # Pattern 2: Factory with one product
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and 'factory' in node.name.lower():
            returns = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value:
                    if isinstance(child.value, ast.Call) and isinstance(child.value.func, ast.Name):
                        returns.add(child.value.func.id)
            if len(returns) <= 1:
                violations.append(Violation(
                    check_name="over-engineering",
                    line=node.lineno,
                    code=f"def {node.name}(...)",
                    message=f"yagni: factory with one product ({', '.join(returns) or 'unknown'}). Inline it until a second variant exists."
                ))

    # Pattern 3: Pure delegation wrapper
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Return):
                ret = node.body[0]
                if isinstance(ret.value, ast.Call):
                    violations.append(Violation(
                        check_name="over-engineering",
                        line=node.lineno,
                        code=f"def {node.name}(...) -> return ...",
                        message=f"shrink: '{node.name}' is a pure delegation wrapper. Inline or delete."
                    ))

    return violations


def check_file(filepath: str) -> list:
    """Run all 20 checks on a single file. Returns list of Violations."""
    path = Path(filepath)

    if not path.exists():
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    if path.suffix != '.py':
        print(f"SKIP: Not a Python file: {filepath}", file=sys.stderr)
        return []

    lines = path.read_text(encoding='utf-8').splitlines()

    all_violations = []
    all_violations.extend(check_atomic_write(lines, filepath))
    all_violations.extend(check_is_not_none(lines, filepath))
    all_violations.extend(check_signal_timeout(lines, filepath))
    all_violations.extend(check_re_read_before_patch(lines, filepath))
    all_violations.extend(check_secret_detection(lines, filepath))
    all_violations.extend(check_path_safety(lines, filepath))
    all_violations.extend(check_encoding_safety(lines, filepath))
    all_violations.extend(check_exception_granularity(lines, filepath))
    all_violations.extend(check_shell_injection(lines, filepath))
    all_violations.extend(check_nesting_depth(lines, filepath))
    all_violations.extend(check_file_overwrite(lines, filepath))
    all_violations.extend(check_line_length(lines, filepath))
    all_violations.extend(check_trailing_whitespace(lines, filepath))
    all_violations.extend(check_todo_left(lines, filepath))
    all_violations.extend(check_import_safety(lines, filepath))
    # AI Slop detection (C11-C15)
    all_violations.extend(check_hallucinated_import(lines, filepath))
    all_violations.extend(check_dead_code_block(lines, filepath))
    all_violations.extend(check_over_commented(lines, filepath))
    all_violations.extend(check_fake_todo(lines, filepath))
    all_violations.extend(check_hallucinated_api(lines, filepath))
    # Over-engineering detection (C16)
    all_violations.extend(check_over_engineering(lines, filepath))

    return all_violations


def check_file_incremental(filepath: str, cache_dir: str = None) -> tuple[list, bool]:
    """增量检查：文件未变更时跳过"""
    cache_dir = cache_dir or os.environ.get('ECC_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
    cache_path = os.path.join(cache_dir, '.agentshield_cache.json')
    
    cache = _load_checksum_cache(cache_path)
    current_hash = _file_checksum(filepath)
    
    if current_hash and cache.get(filepath) == current_hash:
        return [], True  # 未变更，跳过
    
    violations = check_file(filepath)
    
    # 更新缓存
    if current_hash:
        cache[filepath] = current_hash
        _save_checksum_cache(cache_path, cache)
    
    return violations, False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 agentshield_check.py [--incremental] <filepath>", file=sys.stderr)
        sys.exit(1)

    incremental = "--incremental" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--incremental"]
    
    if not args:
        print("Usage: python3 agentshield_check.py [--incremental] <filepath>", file=sys.stderr)
        sys.exit(1)
    
    filepath = args[0]
    
    if incremental:
        violations, skipped = check_file_incremental(filepath)
        if skipped:
            print(f"⏭ SKIP (unchanged): {filepath}")
            sys.exit(0)
    else:
        violations = check_file(filepath)

    if not violations:
        print(f"✓ CLEAN: {filepath}")
        sys.exit(0)

    print(f"✗ VIOLATIONS FOUND in {filepath}")
    print(f"  Total: {len(violations)} issue(s)")
    print()

    for v in violations:
        print(v)
        print()

    sys.exit(1)


if __name__ == "__main__":
    main()
