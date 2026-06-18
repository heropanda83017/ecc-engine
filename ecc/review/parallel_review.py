#!/usr/bin/env python3
"""
parallel_review.py — SPEC-REVIEWER + CODE-QUALITY-REVIEWER 并行审查

在 ECC 流水线中，SPEC-REVIEWER（规范对齐）和 CODE-QUALITY-REVIEWER（质量审查）
互不依赖，可以并行运行。本脚本将两个审查同时启动，聚合结果后输出。

用法:
    python3 parallel_review.py <代码上下文>

    # 从 ENGINE 输出管道传入
    python3 parallel_review.py "$(cat /tmp/engine_output.md)"

输出:
    - 聚合审查报告（stdout）
    - 退出码: 0=全部通过, 1=有条件通过, 2=失败
"""

import sys, subprocess, json, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ecc import config
BASE = config._ECC_HOME
TRI_ROLE = BASE / "ecc" / "core" / "tri_role.py"


def run_review(role: str, context: str) -> dict:
    """运行单个审查角色，返回结果"""
    start = datetime.now()
    try:
        result = subprocess.run(
            [sys.executable, str(TRI_ROLE), role, context],
            capture_output=True, text=True, timeout=600  # 10min timeout per role
        )
        elapsed = (datetime.now() - start).total_seconds()
        return {
            "role": role,
            "verdict": parse_verdict(result.stdout),
            "output": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "elapsed_s": round(elapsed, 1),
            "error": None,
        }
    except subprocess.TimeoutExpired:
        elapsed = (datetime.now() - start).total_seconds()
        return {
            "role": role,
            "verdict": "timeout",
            "output": "",
            "stderr": f"超时 ({elapsed:.0f}s)",
            "exit_code": -1,
            "elapsed_s": round(elapsed, 1),
            "error": "timeout",
        }
    except Exception as e:
        return {
            "role": role,
            "verdict": "error",
            "output": "",
            "stderr": str(e),
            "exit_code": -1,
            "elapsed_s": 0,
            "error": str(e),
        }


def parse_verdict(output: str) -> str:
    """解析判决结果"""
    if not output:
        return "fail"
    first_word = output.strip().split()[0].upper()
    if "APPROVED" in first_word:
        return "approved"
    elif "CONDITIONAL" in first_word:
        return "conditional"
    return "fail"


def aggregate_verdict(results: list[dict]) -> str:
    """聚合多个审查结果：
    - 全部 approved → approved
    - 任一 fail → fail
    - 其余 → conditional
    """
    verdicts = [r["verdict"] for r in results]
    if all(v == "approved" for v in verdicts):
        return "approved"
    if any(v == "fail" for v in verdicts):
        return "fail"
    if any(v == "timeout" for v in verdicts):
        return "conditional"  # 超时降级为 conditional
    return "conditional"


def format_report(results: list[dict]) -> str:
    """生成聚合审查报告"""
    lines = []
    lines.append("# 并行审查报告")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 并行角色: {', '.join(r['role'] for r in results)}")
    lines.append("")

    # 概览表
    lines.append("## 概览")
    lines.append("\n| 角色 | 判决 | 耗时(s) |")
    lines.append("|:-----|:----:|:-------:|")
    for r in results:
        icon = {"approved": "✅", "conditional": "⚠️", "fail": "❌", "timeout": "⏰", "error": "💥"}
        lines.append(f"| {icon.get(r['verdict'], '❓')} {r['role']} | {r['verdict']} | {r['elapsed_s']} |")
    lines.append("")

    # 聚合判决
    agg = aggregate_verdict(results)
    agg_icon = {"approved": "✅", "conditional": "⚠️", "fail": "❌"}
    lines.append(f"**聚合判决: {agg_icon.get(agg, '❓')} {agg.upper()}**")
    lines.append("")

    # 详细输出
    lines.append("## 详细输出")
    for r in results:
        lines.append(f"\n---\n### {r['role']}")
        if r["error"]:
            lines.append(f"\n**错误**: {r['error']}")
        if r["stderr"]:
            lines.append(f"\n**stderr**:\n```\n{r['stderr'][:500]}\n```")
        if r["output"]:
            lines.append(f"\n**输出**:\n```\n{r['output'][:2000]}\n```")
        lines.append("")

    # 时间节省统计
    total_serial = sum(r["elapsed_s"] for r in results)
    max_parallel = max(r["elapsed_s"] for r in results)
    saved = total_serial - max_parallel
    if saved > 0:
        lines.append(f"\n**⏱ 时间节省**: 串行 {total_serial:.0f}s → 并行 {max_parallel:.0f}s (**节省 {saved:.0f}s, {saved/total_serial*100:.0f}%**)")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法: python3 parallel_review.py <代码上下文>")
        print("       echo '<code>' | python3 parallel_review.py")
        sys.exit(1)

    # 从参数或 stdin 读取上下文
    context = " ".join(sys.argv[1:])
    if not context and not sys.stdin.isatty():
        context = sys.stdin.read().strip()

    if not context:
        print("❌ 请提供代码上下文", file=sys.stderr)
        sys.exit(1)

    roles = ["spec-reviewer", "code-quality-reviewer"]
    results = []

    print(f"parallel_review: 启动 {len(roles)} 个并行审查...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(run_review, role, context): role for role in roles}
        for future in as_completed(futures):
            role = futures[future]
            try:
                result = future.result()
                results.append(result)
                icon = {"approved": "✅", "conditional": "⚠️", "fail": "❌", "timeout": "⏰"}
                print(f"  {icon.get(result['verdict'], '❓')} {role}: {result['verdict']} ({result['elapsed_s']}s)", file=sys.stderr)
            except Exception as e:
                print(f"  ❌ {role}: {e}", file=sys.stderr)
                results.append({
                    "role": role,
                    "verdict": "error",
                    "output": "",
                    "stderr": str(e),
                    "exit_code": -1,
                    "elapsed_s": 0,
                    "error": str(e),
                })

    # 按角色排序输出（spec-reviewer 在前）
    results.sort(key=lambda r: r["role"])

    report = format_report(results)
    print(report)

    agg = aggregate_verdict(results)
    if agg == "approved":
        sys.exit(0)
    elif agg == "conditional":
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
