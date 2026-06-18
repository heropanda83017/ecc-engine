#!/usr/bin/env python3
"""
evolution_analyzer.py — 可观测性驱动的主动进化分析器

从 traces_v2.jsonl + trajectory.jsonl 读取 ECC 运行数据，
自动分析系统性退化趋势、审查模式、cycle time 变化，
输出 actionable 的进化建议。

用法:
    python3 evolution_analyzer.py                     # 默认分析（今日数据）
    python3 evolution_analyzer.py --days 7            # 分析最近7天
    python3 evolution_analyzer.py --json              # JSON 格式输出
    python3 evolution_analyzer.py --cron              # cron 模式（写入报告文件）

输出:
    - stdout: 进化建议报告
    - --cron: 写入 cron/output/ecc-evolution-{date}.md
"""

import json, os, sys, statistics, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
import time
from collections import Counter, defaultdict

# ── 路径 ──
from ecc import config

BASE = config._ECC_HOME
TRACES_FILE = config.TRACES_FILE
TRAJECTORY_FILE = config.TRAJECTORY_FILE
CRON_OUTPUT = config.CRON_OUTPUT


# P1-3: 数据文件缓存（避免重复读盘）
_cache_traces = {"data": None, "ts": 0, "days": -1}
_cache_trajectory = {"data": None, "ts": 0, "days": -1}
_CACHE_TTL_S = 30  # 缓存 30 秒


def load_traces(days: int = 0) -> list[dict]:
    """加载 traces_v2.jsonl，可选按天数过滤（带缓存）"""
    now = time.time()
    if (_cache_traces["data"] is not None and
            now - _cache_traces["ts"] < _CACHE_TTL_S and
            _cache_traces["days"] == days):
        return _cache_traces["data"]

    if not TRACES_FILE.exists():
        print(f"❌ traces_v2.jsonl 不存在: {TRACES_FILE}", file=sys.stderr)
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    entries = []
    with open(TRACES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if cutoff:
                    ts = d.get("ts", "")
                    if ts:
                        try:
                            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if t < cutoff:
                                continue
                        except ValueError:
                            pass
                entries.append(d)
            except json.JSONDecodeError:
                continue
    _cache_traces["data"] = entries
    _cache_traces["ts"] = time.time()
    _cache_traces["days"] = days
    _cache_trajectory["data"] = entries
    _cache_trajectory["ts"] = time.time()
    _cache_trajectory["days"] = days
    return entries


def load_trajectory(days: int = 0) -> list[dict]:
    now = time.time()
    if (_cache_trajectory["data"] is not None and
            now - _cache_trajectory["ts"] < _CACHE_TTL_S and
            _cache_trajectory["days"] == days):
        return _cache_trajectory["data"]
    """加载 trajectory.jsonl"""
    if not TRAJECTORY_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    entries = []
    with open(TRAJECTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if cutoff:
                    ts = d.get("ts", "")
                    if ts:
                        try:
                            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if t < cutoff:
                                continue
                        except ValueError:
                            pass
                entries.append(d)
            except json.JSONDecodeError:
                continue
    return entries


def analyze_traces(traces: list[dict]) -> dict:
    """分析 traces_v2.jsonl 数据"""
    result = {
        "total_spans": len(traces),
        "roles": Counter(),
        "statuses": Counter(),
        "verdicts": Counter(),
        "retry_counts": defaultdict(int),
        "duration_stats": {},
        "stagnation_events": 0,
        "failure_trend": [],
    }

    # 按角色统计
    for d in traces:
        name = d.get("name", "unknown")
        result["roles"][name] += 1
        result["statuses"][d.get("status", "unknown")] += 1

        attr = d.get("attributes", {}) or {}
        if "verdict" in attr:
            result["verdicts"][attr["verdict"]] += 1

        # 统计 retry 分布
        if "retry_" in name:
            parts = name.split("_")
            if parts and parts[-1].isdigit():
                result["retry_counts"][name] += 1

    # 停滞检测：连续相同 verdict 的 final-review 重试
    final_review_spans = [d for d in traces if "final-review" in d.get("name", "")]
    prev_verdict = None
    consecutive_same = 0
    for d in final_review_spans:
        attr = d.get("attributes", {}) or {}
        v = attr.get("verdict", "")
        if v == prev_verdict and v in ("conditional", "fail"):
            consecutive_same += 1
            if consecutive_same >= 2:
                result["stagnation_events"] += 1
        else:
            consecutive_same = 0
        prev_verdict = v

    # 按天统计失败率趋势
    day_fails = defaultdict(int)
    day_total = defaultdict(int)
    for d in traces:
        ts = d.get("ts", "")
        if ts:
            day = ts[:10]  # YYYY-MM-DD
            attr = d.get("attributes", {}) or {}
            v = attr.get("verdict", "")
            if v:
                day_total[day] += 1
                if v in ("conditional", "fail"):
                    day_fails[day] += 1

    for day in sorted(day_total.keys()):
        total = day_total[day]
        fails = day_fails.get(day, 0)
        rate = fails / total * 100 if total > 0 else 0
        result["failure_trend"].append({
            "day": day,
            "total": total,
            "fails": fails,
            "fail_rate": round(rate, 1),
        })

    # 持续时间统计
    durations = [d.get("duration_ns", 0) for d in traces if d.get("duration_ns", 0) > 0]
    if durations:
        result["duration_stats"] = {
            "count": len(durations),
            "mean_s": round(statistics.mean(durations) / 1e9, 3),
            "median_s": round(statistics.median(durations) / 1e9, 3),
            "max_s": round(max(durations) / 1e9, 3),
            "p95_s": round(sorted(durations)[int(len(durations) * 0.95)] / 1e9, 3),
        }

    return result


def analyze_trajectory(trajectory: list[dict]) -> dict:
    """分析 trajectory.jsonl 数据"""
    result = {
        "total_entries": len(trajectory),
        "role_stats": Counter(),
        "verdict_stats": Counter(),
        "retry_distribution": defaultdict(int),
        "avg_duration_by_role": {},
        "cycle_time_trend": [],
    }

    role_durations = defaultdict(list)

    for d in trajectory:
        role = d.get("role", "unknown")
        verdict = d.get("verdict", "unknown")
        result["role_stats"][role] += 1
        result["verdict_stats"][verdict] += 1

        round_num = d.get("round", 1)
        if round_num > 1:
            result["retry_distribution"][role] += 1

        duration = d.get("duration_s", 0)
        if duration > 0:
            role_durations[role].append(duration)

    for role, durs in role_durations.items():
        if durs:
            result["avg_duration_by_role"][role] = {
                "mean_s": round(statistics.mean(durs), 1),
                "median_s": round(statistics.median(durs), 1),
                "count": len(durs),
            }

    return result


def generate_suggestions(trace_analysis: dict, traj_analysis: dict) -> list[dict]:
    """根据分析结果生成进化建议"""
    suggestions = []

    # 1. 失败率趋势
    if trace_analysis["failure_trend"]:
        recent = trace_analysis["failure_trend"][-3:]
        avg_fail_rate = sum(d["fail_rate"] for d in recent) / len(recent)
        if avg_fail_rate > 30:
            suggestions.append({
                "priority": "P0",
                "category": "failure_rate",
                "signal": f"近期审查失败率 {avg_fail_rate:.0f}%（最近 {len(recent)} 天）",
                "suggestion": "审查失败率偏高，建议检查 V4 Pro 模型质量或调整审查 prompt 模板",
                "action": "检查 prompt-templates/FINAL-REVIEW.md 和 REVIEW.md 的判定标准",
            })
        elif avg_fail_rate > 15:
            suggestions.append({
                "priority": "P1",
                "category": "failure_rate",
                "signal": f"近期审查失败率 {avg_fail_rate:.0f}%",
                "suggestion": "失败率在警戒线附近，建议关注趋势变化",
                "action": "监控后续 3 天失败率是否持续上升",
            })

    # 2. 停滞事件
    if trace_analysis["stagnation_events"] > 0:
        suggestions.append({
            "priority": "P0",
            "category": "stagnation",
            "signal": f"检测到 {trace_analysis['stagnation_events']} 次停滞事件（连续相同 verdict 的 final-review 重试）",
            "suggestion": "停滞说明审查反馈未有效推动改进，建议降低停滞检测阈值或增加重试多样性",
            "action": "检查 tri_role.py 的停滞检测逻辑（Jaccard 阈值 0.6）是否过松",
        })

    # 3. Retry 分布
    if traj_analysis["retry_distribution"]:
        total_retries = sum(traj_analysis["retry_distribution"].values())
        if total_retries > 5:
            top_role = max(traj_analysis["retry_distribution"], key=traj_analysis["retry_distribution"].get)
            suggestions.append({
                "priority": "P1",
                "category": "retry_pattern",
                "signal": f"共 {total_retries} 次重试，最多发生在 {top_role}（{traj_analysis['retry_distribution'][top_role]} 次）",
                "suggestion": f"{top_role} 频繁重试，建议检查该角色的 prompt 模板或上下文质量",
                "action": f"检查 prompt-templates/{top_role.upper().replace('-','_')}.md 是否清晰完整",
            })

    # 4. Cycle time 趋势
    if traj_analysis["avg_duration_by_role"]:
        slow_roles = {r: s for r, s in traj_analysis["avg_duration_by_role"].items()
                      if s["mean_s"] > 120}
        if slow_roles:
            role_list = ", ".join(slow_roles.keys())
            suggestions.append({
                "priority": "P1",
                "category": "cycle_time",
                "signal": f"以下角色平均耗时超过 2 分钟: {role_list}",
                "suggestion": "考虑减少这些角色的 prompt 长度或缩小上下文范围",
                "action": f"检查对应 prompt 模板是否包含过多示例或冗余说明",
            })

    # 5. 数据量不足
    if trace_analysis["total_spans"] < 50:
        suggestions.append({
            "priority": "P2",
            "category": "data_volume",
            "signal": f"traces_v2.jsonl 仅 {trace_analysis['total_spans']} 条记录，统计意义有限",
            "suggestion": "持续积累数据后再做趋势分析",
            "action": "无",
        })

    # 6. 模型使用单一
    if len(trace_analysis.get("verdicts", {})) == 0:
        suggestions.append({
            "priority": "P2",
            "category": "model_diversity",
            "signal": "traces 中缺少 verdict 属性，无法分析模型性能",
            "suggestion": "确保 tri_role.py 正确记录 verdict 到 traces",
            "action": "检查 tri_role.py 中 trace span 的 set_attribute('verdict', ...) 调用",
        })

    return suggestions


def format_report(trace_analysis: dict, traj_analysis: dict, suggestions: list[dict]) -> str:
    """生成可读的进化分析报告"""
    lines = []
    lines.append("# ECC 进化分析报告")
    lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据来源: traces_v2.jsonl ({trace_analysis['total_spans']} spans) + trajectory.jsonl ({traj_analysis['total_entries']} entries)")
    lines.append("")

    # ── 概览 ──
    lines.append("## 一、概览")
    lines.append(f"\n| 指标 | 值 |")
    lines.append(f"|:-----|:---|")
    lines.append(f"| traces 总数 | {trace_analysis['total_spans']} |")
    lines.append(f"| trajectory 总数 | {traj_analysis['total_entries']} |")
    trend_str = ', '.join(f'{d["day"]}: {d["fail_rate"]}%' for d in trace_analysis['failure_trend'][-5:]) or '无数据'
    lines.append(f"| 审查失败率趋势 | {trend_str} |")
    lines.append(f"| 停滞事件 | {trace_analysis['stagnation_events']} |")

    if trace_analysis["duration_stats"]:
        ds = trace_analysis["duration_stats"]
        lines.append(f"| 平均 span 耗时 | {ds['mean_s']}s |")
        lines.append(f"| P95 span 耗时 | {ds['p95_s']}s |")

    lines.append("")

    # ── 角色分布 ──
    lines.append("## 二、角色分布")
    lines.append("\n| 角色 | 调用次数 |")
    lines.append("|:-----|:--------:|")
    for role, count in trace_analysis["roles"].most_common(15):
        lines.append(f"| {role} | {count} |")
    lines.append("")

    # ── Verdict 分布 ──
    lines.append("## 三、Verdict 分布")
    lines.append("\n| 判决 | 次数 |")
    lines.append("|:-----|:----:|")
    for v, c in trace_analysis["verdicts"].most_common():
        lines.append(f"| {v} | {c} |")
    lines.append("")

    # ── Retry 分布 ──
    if traj_analysis["retry_distribution"]:
        lines.append("## 四、Retry 分布")
        lines.append("\n| 角色 | 重试次数 |")
        lines.append("|:-----|:--------:|")
        for role, count in sorted(traj_analysis["retry_distribution"].items(), key=lambda x: -x[1]):
            lines.append(f"| {role} | {count} |")
        lines.append("")

    # ── 角色耗时 ──
    if traj_analysis["avg_duration_by_role"]:
        lines.append("## 五、角色平均耗时")
        lines.append("\n| 角色 | 平均(s) | 中位数(s) | 样本数 |")
        lines.append("|:-----|:-------:|:---------:|:------:|")
        for role, stats in sorted(traj_analysis["avg_duration_by_role"].items(), key=lambda x: -x[1]["mean_s"]):
            lines.append(f"| {role} | {stats['mean_s']} | {stats['median_s']} | {stats['count']} |")
        lines.append("")

    # ── 进化建议 ──
    lines.append("## 六、进化建议")
    if suggestions:
        lines.append("\n| 优先级 | 类别 | 信号 | 建议 |")
        lines.append("|:------:|:-----|:-----|:-----|")
        for s in suggestions:
            lines.append(f"| **{s['priority']}** | {s['category']} | {s['signal']} | {s['suggestion']} |")
        lines.append("")
        lines.append("### 详细行动项")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"\n**{i}. [{s['priority']}] {s['category']}**")
            lines.append(f"   - 信号: {s['signal']}")
            lines.append(f"   - 建议: {s['suggestion']}")
            lines.append(f"   - 行动: {s['action']}")
    else:
        lines.append("\n✅ 未发现需要改进的问题。系统运行正常。")
    lines.append("")

    # ── 失败率趋势详情 ──
    if trace_analysis["failure_trend"]:
        lines.append("## 七、失败率趋势")
        lines.append("\n| 日期 | 总数 | 失败数 | 失败率 |")
        lines.append("|:-----|:----:|:------:|:------:|")
        for d in trace_analysis["failure_trend"]:
            marker = " ⚠️" if d["fail_rate"] > 30 else ""
            lines.append(f"| {d['day']} | {d['total']} | {d['fails']} | {d['fail_rate']}%{marker} |")
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ECC 可观测性进化分析器")
    parser.add_argument("--days", type=int, default=0, help="分析最近 N 天（0=全部）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--cron", action="store_true", help="cron 模式：写入报告文件")
    args = parser.parse_args()

    traces = load_traces(days=args.days)
    trajectory = load_trajectory(days=args.days)

    if not traces and not trajectory:
        print("❌ 无数据可分析。请先运行 tri_role.py 生成 traces 和 trajectory 数据。")
        sys.exit(1)

    trace_analysis = analyze_traces(traces)
    traj_analysis = analyze_trajectory(trajectory)
    suggestions = generate_suggestions(trace_analysis, traj_analysis)

    if args.json:
        output = {
            "timestamp": datetime.now().isoformat(),
            "trace_analysis": {
                "total_spans": trace_analysis["total_spans"],
                "verdicts": dict(trace_analysis["verdicts"]),
                "stagnation_events": trace_analysis["stagnation_events"],
                "failure_trend": trace_analysis["failure_trend"],
                "duration_stats": trace_analysis["duration_stats"],
            },
            "trajectory_analysis": {
                "total_entries": traj_analysis["total_entries"],
                "retry_distribution": dict(traj_analysis["retry_distribution"]),
                "avg_duration_by_role": traj_analysis["avg_duration_by_role"],
            },
            "suggestions": suggestions,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        report = format_report(trace_analysis, traj_analysis, suggestions)
        print(report)

    # cron 模式：写入报告文件
    if args.cron:
        date_str = datetime.now().strftime("%Y-%m-%d")
        CRON_OUTPUT.mkdir(parents=True, exist_ok=True)
        report_path = CRON_OUTPUT / f"ecc-evolution-{date_str}.md"
        report = format_report(trace_analysis, traj_analysis, suggestions)
        report_path.write_text(report, encoding="utf-8")
        print(f"\n✅ 报告已写入: {report_path}", file=sys.stderr)

    # 如果有 P0 建议，非零退出码（仅在非 cron 模式）
    p0_count = sum(1 for s in suggestions if s["priority"] == "P0")
    if p0_count > 0 and not args.cron:
        sys.exit(2)


if __name__ == "__main__":
    main()
