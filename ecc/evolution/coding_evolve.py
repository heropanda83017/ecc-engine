#!/usr/bin/env python3
"""
coding_evolve.py — ECC 编码模式进化仪表盘

子命令:
    trends   读取 session_metrics.csv，展示通过/失败率趋势
    gaps     比较 agentshield 违规发现 vs 已记录的模式，为缺口建议新模式
    suggest   基于 session_metrics 趋势输出改进建议

用法:
    python3 coding_evolve.py trends
    python3 coding_evolve.py gaps
    python3 coding_evolve.py suggest
"""

import os, sys, csv, json, logging, re
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Optional, Tuple
from collections import Counter, defaultdict

# ── 路径 ──
SCRIPTS = Path(__file__).parent.resolve()
PROFILE = SCRIPTS.parent
DATA_DIR = PROFILE / "data"
INSTINCTS = PROFILE / "instincts"

METRICS_CSV = DATA_DIR / "session_metrics.csv"
CODING_YAML = INSTINCTS / "coding-patterns.yaml"
AGENTSHIELD_SCRIPT = SCRIPTS / "agentshield_check.py"

log = logging.getLogger("coding_evolve")


# ═══════════════════════════════════════════════════════════
# trends — 读取 session_metrics.csv，展示通过/失败率趋势
# ═══════════════════════════════════════════════════════════

def load_metrics() -> List[Dict]:
    """加载 session_metrics.csv"""
    if not METRICS_CSV.exists():
        return []
    rows = []
    with open(METRICS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def cmd_trends():
    """展示通过/失败率趋势"""
    rows = load_metrics()
    if not rows:
        print("📭 session_metrics.csv 无数据或文件不存在")
        print(f"   路径: {METRICS_CSV}")
        return

    total = len(rows)
    verdicts = Counter(r.get("verdict", "unknown") for r in rows)
    roles = Counter(r.get("role", "unknown") for r in rows)

    approved = verdicts.get("approved", 0)
    conditional = verdicts.get("conditional", 0)
    fail = verdicts.get("fail", 0)
    unknown = verdicts.get("unknown", 0)

    pass_rate = approved / total * 100 if total > 0 else 0
    cond_rate = conditional / total * 100 if total > 0 else 0
    fail_rate = fail / total * 100 if total > 0 else 0

    print("=" * 60)
    print(f"📊 ECC 编码模式 — 会话度量趋势 ({METRICS_CSV.name})")
    print("=" * 60)
    print(f"总记录数:      {total}")
    print(f"  通过 ✅:    {approved:3d}  ({pass_rate:5.1f}%)")
    print(f"  条件通过 🟡: {conditional:3d}  ({cond_rate:5.1f}%)")
    print(f"  失败 ❌:    {fail:3d}  ({fail_rate:5.1f}%)")
    if unknown:
        print(f"  未知 ❓:    {unknown:3d}")
    print()

    # 按角色统计
    print("按角色分布:")
    for role, count in roles.most_common():
        print(f"  {role:15s} {count:3d} 次  ({count/total*100:5.1f}%)")
    print()

    # 时序趋势（按日期聚合）
    daily = defaultdict(lambda: {"total": 0, "approved": 0, "conditional": 0, "fail": 0})
    for r in rows:
        ts = r.get("timestamp", "")
        day = ts[:10] if ts else "unknown"
        v = r.get("verdict", "unknown")
        daily[day]["total"] += 1
        if v == "approved":
            daily[day]["approved"] += 1
        elif v == "conditional":
            daily[day]["conditional"] += 1
        elif v == "fail":
            daily[day]["fail"] += 1

    sorted_days = sorted(daily.keys())
    if len(sorted_days) >= 2:
        print("时序趋势（逐日）:")
        print(f"  {'日期':12s} {'总数':>4s} {'通过':>4s} {'条件':>4s} {'失败':>4s} {'通过率':>6s}")
        print(f"  {'-'*36}")
        for day in sorted_days:
            d = daily[day]
            dr = d["approved"] / d["total"] * 100 if d["total"] > 0 else 0
            print(f"  {day:12s} {d['total']:4d} {d['approved']:4d} {d['conditional']:4d} {d['fail']:4d} {dr:5.1f}%")
        print()

    # 总体健康度评分
    health_score = approved / total * 100 - fail * 5
    health_score = max(0, min(100, health_score))
    bar_len = int(health_score / 10)
    bar = "🟩" * bar_len + "⬜" * (10 - bar_len)
    print(f"健康度评分: {bar} {health_score:.0f}/100")
    if pass_rate >= 80:
        print("✅ 总体健康 — 编码模式质量良好")
    elif pass_rate >= 60:
        print("⚠️  需关注 — 条件通过和失败较多，建议审查编码模式")
    else:
        print("❌ 需干预 — 失败率偏高，建议优先补充编码模式")


# ═══════════════════════════════════════════════════════════
# gaps — 比较 agentshield 违规 vs 已记录的模式
# ═══════════════════════════════════════════════════════════

def get_recorded_patterns() -> List[str]:
    """从 coding-patterns.yaml 获取已记录的模式名称列表"""
    if not CODING_YAML.exists():
        return []
    try:
        import yaml
        with open(CODING_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return [o.get("pattern", "") for o in data.get("observations", [])]
    except Exception:
        return []


def get_agentshield_checks() -> Dict[str, str]:
    """从 agentshield_check.py 提取检查项名称和描述"""
    if not AGENTSHIELD_SCRIPT.exists():
        return {}
    checks = {}
    try:
        with open(AGENTSHIELD_SCRIPT, "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r"check_name=['\"]([^'\"]+)['\"]", line)
                if m:
                    check_name = m.group(1)
                    checks[check_name] = f"agentshield_check 已实现，但未在 coding-patterns.yaml 中记录"
    except Exception:
        pass
    return checks


def cmd_gaps():
    """比较 agentshield 违规发现 vs 已记录的模式"""

    recorded = get_recorded_patterns()
    checks = get_agentshield_checks()

    print("=" * 60)
    print("🔍 ECC 编码模式 — 缺口分析 (Pattern Gaps)")
    print("=" * 60)

    # 检查 agentshield 实现的检查是否都有对应的 pattern 记录
    gaps_found = False
    for check_name, desc in checks.items():
        if check_name not in recorded:
            gaps_found = True
            print(f"\n⚠️  缺口: agentshield 检查 '{check_name}' 无对应编码模式记录")
            print(f"   建议: 运行以下命令添加:")
            suggested_pattern = check_name
            print(f"         python3 coding_scout.py record \\")
            print(f"           --pattern \"{suggested_pattern}\" \\")
            print(f"           --desc \"{check_name}: {desc}\" \\")
            print(f"           --trigger \"agentshield 自动检测\" \\")
            print(f"           --confidence 0.7")

    if not gaps_found:
        print("\n✅ 所有 agentshield 检查项在 coding-patterns.yaml 中均有对应模式")

    # 反过来检查：已记录的模式中是否有 agentshield 未实现的
    print()
    shield_checks = set(checks.keys())
    for p in recorded:
        if p and p not in shield_checks:
            print(f"📌 提示: 模式 '{p}' 已记录但无 agentshield 自动化检查")
            print(f"   建议: 在 agentshield_check.py 中增加对应检查函数")

    # 统计缺口概况
    missing = len([c for c in checks.keys() if c not in recorded])
    print(f"\n{'─' * 40}")
    print(f"已记录模式: {len(recorded)} 个")
    print(f"agentshield 检查: {len(checks)} 项")
    print(f"模式缺口: {missing} 个")


# ═══════════════════════════════════════════════════════════
# suggest — 基于 session_metrics 趋势输出改进建议
# ═══════════════════════════════════════════════════════════

def cmd_suggest():
    """基于 session_metrics 趋势输出改进建议"""
    rows = load_metrics()
    if not rows:
        print("📭 无 session_metrics.csv 数据，无法生成建议")
        return

    total = len(rows)
    verdicts = Counter(r.get("verdict", "unknown") for r in rows)
    roles = Counter(r.get("role", "unknown") for r in rows)

    approved = verdicts.get("approved", 0)
    conditional = verdicts.get("conditional", 0)
    fail = verdicts.get("fail", 0)
    pass_rate = approved / total * 100 if total > 0 else 0

    print("=" * 60)
    print("💡 ECC 编码模式 — 改进建议 (Suggestions)")
    print("=" * 60)
    print()

    suggestions = []

    # 1. 失败率过高
    if fail > 0 and fail / total > 0.3:
        suggestions.append((
            "🔴 高优先级",
            f"失败率 {fail/total*100:.0f}% 偏高（{fail}/{total} 次）",
            "建议审查失败原因，补充对应的编码模式。"
            " 运行 coding_scout.py record 记录失败模式，提高审查质量。"
        ))

    # 2. 条件通过多
    if conditional > 0 and conditional / total > 0.4:
        suggestions.append((
            "🟡 中优先级",
            f"条件通过率 {conditional/total*100:.0f}% 偏高（{conditional}/{total} 次）",
            "条件通过通常意味着代码需要微调。建议分析高频条件通过的角色，"
            "更新对应的 prompt 模板以减少修正循环。"
        ))

    # 3. 有角色从未通过
    for role, count in roles.most_common():
        role_approved = sum(1 for r in rows if r.get("role") == role and r.get("verdict") == "approved")
        if count > 2 and role_approved == 0:
            suggestions.append((
                "🟡 中优先级",
                f"角色 '{role}' 从未通过审查（{count} 次全部失败或条件通过）",
                f"建议检查 {role} 的 prompt 模板模板质量，或补充相关编码模式以降低失败率"
            ))

    # 4. 数据量不足
    if total < 5:
        suggestions.append((
            "ℹ️ 低优先级",
            f"数据点仅 {total} 个，趋势分析置信度不足",
            "建议运行更多 tri_role 审查以积累数据。达到 20+ 样本时趋势分析更有意义"
        ))

    # 5. 没有条件通过 — 可能阈值太宽松
    if conditional == 0 and total >= 5 and pass_rate < 100:
        suggestions.append((
            "ℹ️ 低优先级",
            "没有条件通过记录，只有 approved 或 fail",
            "条件通过是重要的中间状态。如果系统没有记录条件通过，"
            "可能是 verdict 判定阈值过于严格。检查 tri_role.py 的 verdict 逻辑"
        ))

    # 6. 健康度低
    if pass_rate < 60 and total >= 5:
        suggestions.append((
            "🔴 高优先级",
            f"通过率仅 {pass_rate:.0f}%，编码模式体系整体偏低",
            "建议优先执行以下操作:\n"
            "  1. python3 coding_scout.py list  — 检查现有模式\n"
            "  2. python3 coding_evolve.py gaps — 检查模式缺口\n"
            "  3. python3 decay_coding_patterns.py — 衰减旧模式置信度"
        ))

    if not suggestions:
        suggestions.append((
            "✅ 无建议",
            "当前状态良好",
            "编码模式体系运行正常，无需特别调整"
        ))

    for severity, title, detail in suggestions:
        print(f"{severity} {title}")
        print(f"   {detail}")
        print()

    # 执行摘要
    print(f"{'─' * 40}")
    print(f"总记录: {total} | 通过: {approved} | 条件: {conditional} | 失败: {fail}")
    print(f"通过率: {pass_rate:.1f}%")


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "trends":
        cmd_trends()
    elif cmd == "gaps":
        cmd_gaps()
    elif cmd == "suggest":
        cmd_suggest()
    else:
        print(f"未知子命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
