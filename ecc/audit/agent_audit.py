#!/usr/bin/env python3
"""
agent_audit.py — 12 层 Agent 系统诊断审计

借鉴 ECC-Hermes agent-architecture-audit skill，对 Agent 系统进行
12 层结构化诊断，发现性能退化根因。

用法:
    python3 agent_audit.py              # 标准诊断
    python3 agent_audit.py --quick      # 快速诊断（仅 6 层）
    python3 agent_audit.py --json       # JSON 输出
"""

import os, sys, json, subprocess
from pathlib import Path
from datetime import datetime

from ecc import config

BASE = config._ECC_HOME
SCRIPTS = BASE / "ecc"
DATA = config.DATA_DIR

# ── 12 层诊断框架 ──
LAYERS = [
    {
        "id": "system_prompt",
        "name": "系统提示词",
        "desc": "检查系统 prompt 是否被 wrapper 层降级",
        "check": "prompt-templates/ 是否存在，模板是否完整",
    },
    {
        "id": "session_history",
        "name": "会话历史",
        "desc": "旧会话上下文是否泄漏到新议程",
        "check": "trajectory.jsonl / session 文件是否膨胀",
    },
    {
        "id": "memory",
        "name": "记忆污染",
        "desc": "检查 memory 中是否残留过期信息",
        "check": "memory 条目是否超过 90% 容量阈值",
    },
    {
        "id": "tool_selection",
        "name": "工具选择纪律",
        "desc": "Agent 是否跳过声明工具直接回答",
        "check": "MCP 工具调用日志中是否有工具未调用的模式",
    },
    {
        "id": "execution",
        "name": "执行质量",
        "desc": "工具调用是否正确执行",
        "check": "traces_v2.jsonl 中调用失败率",
    },
    {
        "id": "interpretation",
        "name": "结果解释",
        "desc": "Agent 是否正确解读工具输出",
        "check": "tri_role 审查通过率",
    },
    {
        "id": "answer_shaping",
        "name": "答案塑造",
        "desc": "输出是否被过度修剪或添加无关内容",
        "check": "--fast 模式 vs 完整模式的输出对比",
    },
    {
        "id": "cost_efficiency",
        "name": "成本效率",
        "desc": "Token 消耗是否合理",
        "check": "model_router 调用统计",
    },
    {
        "id": "regression",
        "name": "退化检测",
        "desc": "功能是否随时间退化",
        "check": "evolution_analyzer 失败率趋势",
    },
    {
        "id": "feedback_loop",
        "name": "反馈闭环",
        "desc": "审查->修复的闭环是否有效",
        "check": "停滞事件次数",
    },
    {
        "id": "persistence",
        "name": "持久化",
        "desc": "跨 session 状态是否完整",
        "check": "failure_db 恢复成功率",
    },
    {
        "id": "security",
        "name": "安全基线",
        "desc": "agentshield 安全规则是否生效",
        "check": "agentshield 违规数趋势",
    },
]

SEVERITY = {0: "OK", 1: "INFO", 2: "WARN", 3: "CRITICAL"}


def run_check(layer: dict, quick: bool = False) -> dict:
    """对单层执行诊断检查"""
    lid = layer["id"]
    status = 0
    evidence = []
    suggestions = []

    if lid == "session_history":
        traj_file = DATA / "trajectory.jsonl"
        if traj_file.exists():
            lines = traj_file.read_text(encoding="utf-8").count("\n")
            status = 2 if lines > 200 else (1 if lines > 100 else 0)
            evidence = [f"trajectory.jsonl: {lines} entries"]
            if status >= 2:
                suggestions.append("考虑归档旧 trajectory 日志")

    elif lid == "memory":
        mem_path = BASE / "memory" / "memory.md"
        if mem_path.exists():
            chars = len(mem_path.read_text(encoding="utf-8"))
            pct = chars / 2200 * 100
            status = 2 if pct > 90 else (1 if pct > 70 else 0)
            evidence = [f"memory: {chars} chars / 2200 ({pct:.0f}%)"]
            if status >= 2:
                suggestions.append("清理过期 memory 条目，腾出空间")

    elif lid == "execution":
        traces_file = DATA / "traces_v2.jsonl"
        if traces_file.exists():
            total = 0
            errors = 0
            for line in traces_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    total += 1
                    if entry.get("status") != "OK":
                        errors += 1
                except json.JSONDecodeError:
                    continue
            err_rate = errors / total * 100 if total > 0 else 0
            status = 2 if err_rate > 10 else (1 if err_rate > 5 else 0)
            evidence = [f"traces: {errors}/{total} errors ({err_rate:.1f}%)"]
            if status >= 2:
                suggestions.append("检查高频失败的工具调用")

    elif lid == "interpretation":
        # 从 evolution_analyzer 获取通过率
        try:
            sys.path.insert(0, str(SCRIPTS))
            from evolution_analyzer import load_trajectory, analyze_trajectory
            traj = load_trajectory(days=3)
            if traj:
                analysis = analyze_trajectory(traj)
                total = sum(analysis.get("verdict_stats", {}).values())
                fails = analysis.get("verdict_stats", {}).get("fail", 0) + \
                        analysis.get("verdict_stats", {}).get("conditional", 0)
                pass_rate = (total - fails) / total * 100 if total > 0 else 0
                status = 2 if pass_rate < 30 else (1 if pass_rate < 60 else 0)
                evidence = [f"审查通过率: {pass_rate:.0f}% ({total-fails}/{total})"]
        except Exception:
            pass

    elif lid == "security":
        # 检查 agentshield 违规趋势
        evo = DATA / "traces_v2.jsonl"
        if evo.exists():
            total = 0
            for line in evo.read_text(encoding="utf-8").splitlines():
                if "check_" in line or "violation" in line.lower():
                    total += 1
            status = 2 if total > 10 else 0
            evidence = [f"检测到 ~{total} 个安全相关事件"]

    elif lid == "feedback_loop":
        try:
            sys.path.insert(0, str(SCRIPTS))
            from evolution_analyzer import load_traces, analyze_traces
            traces = load_traces(days=3)
            if traces:
                analysis = analyze_traces(traces)
                stagnation = analysis.get("stagnation_events", 0)
                status = 2 if stagnation > 3 else (1 if stagnation > 1 else 0)
                evidence = [f"停滞事件: {stagnation}"]
        except Exception:
            pass

    elif lid == "regression":
        try:
            sys.path.insert(0, str(SCRIPTS))
            from evolution_analyzer import load_trajectory, analyze_trajectory
            traj = load_trajectory(days=5)
            if traj:
                analysis = analyze_trajectory(traj)
                role_times = analysis.get("avg_duration_by_role", {})
                fr_time = role_times.get("final-review", {}).get("mean_s", 0)
                status = 2 if fr_time > 120 else (1 if fr_time > 60 else 0)
                evidence = [f"FINAL-REVIEW 平均: {fr_time:.0f}s"]
        except Exception:
            pass

    elif lid == "persistence":
        fail_db = DATA / "failure_db.jsonl"
        if fail_db.exists():
            lines = fail_db.read_text(encoding="utf-8").strip()
            count = len([l for l in lines.split('\n') if l.strip()]) if lines else 0
            status = 1 if count > 0 else 0
            evidence = [f"failure_db: {count} 条记录"]

    elif lid == "system_prompt":
        tmpl_dir = BASE / "prompt-templates"
        files = list(tmpl_dir.glob("*.md"))
        missing = [f.name for f in tmpl_dir.glob("*") if not f.name.endswith(".md")]
        status = 1 if len(missing) > 0 else 0
        evidence = [f"模板数: {len(files)}"]
        if missing:
            status = 2
            evidence.append(f"非模板文件: {missing}")

    elif lid == "tool_selection":
        wudao = None
        for s in ["wudao", "stock", "kms"]:
            fp = DATA / f"traces_v2.jsonl"
            if fp.exists():
                # Check MCP tool coverage
                count = 0
                for line in fp.read_text(encoding="utf-8").splitlines():
                    if "tool_call" in line.lower():
                        count += 1
                status = 1 if count < 5 else 0
                evidence = [f"MCP 工具调用: ~{count}"]
                break

    elif lid == "cost_efficiency":
        cost_file = DATA / "session_metrics.csv"
        if cost_file.exists():
            lines = cost_file.read_text(encoding="utf-8").strip().split("\n")
            status = 1 if len(lines) > 100 else 0
            evidence = [f"会话记录: {len(lines)} 条"]

    elif lid == "answer_shaping":
        # Compare prompt sizes between --fast and full mode
        full_size = 0
        fast_size = 0
        rules_dir = BASE / "rules"
        for rf in rules_dir.glob("*.md"):
            full_size += len(rf.read_text(encoding="utf-8"))
        status = 1 if full_size > 5000 else 0
        evidence = [f"rules 总大小: {full_size} 字符"]
        if status > 0:
            suggestions.append("--fast 模式可跳过 rules 注入，节省 ~7KB")

    if not evidence:
        evidence.append("未检查（需运行时数据）")

    return {
        "id": lid,
        "name": layer["name"],
        "status": status,
        "severity": SEVERITY.get(status, "UNKNOWN"),
        "evidence": evidence,
        "suggestions": suggestions,
    }


def diagnose(quick: bool = False) -> dict:
    """执行完整诊断"""
    results = []
    layers_to_check = LAYERS[:6] if quick else LAYERS

    for layer in layers_to_check:
        result = run_check(layer, quick)
        results.append(result)
        icon = {0: "✅", 1: "ℹ️", 2: "⚠️", 3: "🔴"}.get(result["status"], "❓")
        print(f"  {icon} [{result['severity']}] {result['name']}: {result['evidence'][0] if result['evidence'] else '?'}")
        for s in result["suggestions"]:
            print(f"     → {s}")

    # 计算健康评分
    scores = [max(0, 5 - r["status"] * 1.5) for r in results]
    health = round(sum(scores) / len(scores) * 10, 1) if scores else 0

    return {
        "timestamp": datetime.now().isoformat(),
        "quick": quick,
        "layers_checked": len(results),
        "critical": sum(1 for r in results if r["status"] >= 3),
        "warnings": sum(1 for r in results if r["status"] >= 2),
        "health_score": health,
        "layers": results,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="12 层 Agent 系统诊断")
    parser.add_argument("--quick", action="store_true", help="快速诊断（6 层）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    print(f"\n🔍 Agent 系统诊断 ({'快速' if args.quick else '完整'}模式)")
    print(f"   12 层框架: {', '.join(l['name'] for l in (LAYERS[:6] if args.quick else LAYERS))}")
    print()

    result = diagnose(quick=args.quick)

    print(f"\n健康评分: {result['health_score']}/10")
    print(f"  关键问题: {result['critical']} | 警告: {result['warnings']}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 退出码
    if result["critical"] > 0:
        sys.exit(2)
    elif result["warnings"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
