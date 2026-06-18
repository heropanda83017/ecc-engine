#!/usr/bin/env python3
"""
debt_scanner.py — 扫描 `ponytail:` 注释生成债务清单

借鉴 Ponytail 的 ponytail-debt skill，自动扫描代码库中所有 `ponytail:` 注释，
按文件分组输出债务清单，标注天花板和升级路径。

用法:
    python3 debt_scanner.py [目录路径]
    python3 debt_scanner.py --json    # JSON 格式输出
    python3 debt_scanner.py --watch   # 监控模式（扫描 + 写入 PONYTAIL-DEBT.md）
"""

import os, sys, re, json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ── 默认扫描路径 ──
DEFAULT_DIRS = [
    Path.cwd(),
]

# ponytail: 注释的正则, 升级到 ast 解析 if false positives become an issue
PONYTAIL_COMMENT_RE = re.compile(
    r'(#|//|--|<!--)\s*ponytail:\s*(.+?)(?:\n|$)', re.IGNORECASE
)

# 无升级路径的标记
NO_TRIGGER_TAGS = {"no-trigger", "tbd", "todo", "later", "fixme"}


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个文件中的 ponytail: 注释"""
    findings = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return findings

    for i, line in enumerate(content.split("\n"), 1):
        m = PONYTAIL_COMMENT_RE.search(line)
        if m:
            comment_text = m.group(2).strip()
            # 解析天花板和升级路径
            parts = comment_text.split(",")
            ceiling = parts[0].strip() if parts else ""
            upgrade = parts[1].strip() if len(parts) > 1 else ""

            # 检测是否缺少升级路径
            has_trigger = bool(upgrade) and upgrade.lower() not in NO_TRIGGER_TAGS

            findings.append({
                "file": str(filepath),
                "line": i,
                "ceiling": ceiling,
                "upgrade_path": upgrade,
                "has_trigger": has_trigger,
                "code": line.strip()[:100],
            })

    return findings


def scan_directory(directory: Path, extensions: set = None) -> list[dict]:
    """递归扫描目录"""
    if extensions is None:
        extensions = {".py", ".js", ".ts", ".rs", ".go", ".java", ".sh", ".md", ".yaml", ".yml", ".json"}

    all_findings = []
    for root, dirs, files in os.walk(directory):
        # 跳过常见忽略目录
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv")]
        for fname in files:
            ext = Path(fname).suffix
            if ext in extensions:
                fp = Path(root) / fname
                all_findings.extend(scan_file(fp))

    return all_findings


def format_report(findings: list[dict]) -> str:
    """生成可读的债务清单报告"""
    lines = []
    lines.append("# Ponytail 债务清单")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 共 {len(findings)} 个 `ponytail:` 注释")
    lines.append("")

    # 按文件分组
    by_file = defaultdict(list)
    for f in findings:
        by_file[f["file"]].append(f)

    # 统计
    no_trigger_count = sum(1 for f in findings if not f["has_trigger"])
    lines.append(f"**总标记**: {len(findings)} | **无升级路径**: {no_trigger_count} ⚠️")
    lines.append("")

    # 按文件输出
    for filepath, file_findings in sorted(by_file.items()):
        # 简化路径
        short_path = filepath
        for d in DEFAULT_DIRS:
            d_str = str(d)
            if filepath.startswith(d_str):
                short_path = "..." + filepath[len(d_str):]
                break

        lines.append(f"### {short_path}")
        lines.append("")
        lines.append("| 行号 | 天花板 | 升级路径 | 触发条件 |")
        lines.append("|:----:|:-------|:---------|:--------:|")
        for f in file_findings:
            trigger_mark = "✅" if f["has_trigger"] else "⚠️"
            upgrade = f["upgrade_path"] if f["upgrade_path"] else "—"
            lines.append(f"| {f['line']} | {f['ceiling']} | {upgrade} | {trigger_mark} |")
        lines.append("")

    # 无升级路径的标记
    no_trigger = [f for f in findings if not f["has_trigger"]]
    if no_trigger:
        lines.append("## ⚠️ 无升级路径的标记（静默腐烂风险）")
        lines.append("")
        for f in no_trigger:
            lines.append(f"- `{f['file']}:{f['line']}` — {f['ceiling']}")
        lines.append("")

    lines.append("---")
    lines.append(f"*debt_scanner.py — {len(findings)} markers, {no_trigger_count} with no trigger*")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="扫描 ponytail: 注释生成债务清单")
    parser.add_argument("paths", nargs="*", help="要扫描的目录或文件路径")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--watch", action="store_true", help="写入 PONYTAIL-DEBT.md")
    args = parser.parse_args()

    # 确定扫描路径
    scan_paths = []
    if args.paths:
        for p in args.paths:
            scan_paths.append(Path(p))
    else:
        scan_paths = DEFAULT_DIRS

    # 扫描
    all_findings = []
    for sp in scan_paths:
        if sp.is_file():
            all_findings.extend(scan_file(sp))
        elif sp.is_dir():
            all_findings.extend(scan_directory(sp))
        else:
            print(f"⚠️ 路径不存在: {sp}", file=sys.stderr)

    if args.json:
        print(json.dumps(all_findings, ensure_ascii=False, indent=2))
    else:
        report = format_report(all_findings)
        print(report)

    # --watch 模式写入文件
    if args.watch:
        report_path = Path.cwd() / "PONYTAIL-DEBT.md"
        report_path.write_text(format_report(all_findings), encoding="utf-8")
        print(f"\n✅ 债务清单已写入: {report_path}", file=sys.stderr)

    # 如果有无升级路径的标记，非零退出
    no_trigger = sum(1 for f in all_findings if not f["has_trigger"])
    sys.exit(2 if no_trigger > 0 else 0)


if __name__ == "__main__":
    main()
