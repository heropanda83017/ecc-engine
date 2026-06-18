#!/usr/bin/env python3
"""
system_health_check.py — Loop 体系完整健康检查 (v1, 2026-06-10)

检查整个 Agent Loop 框架 3 层（ECC + Harness + 仪表盘）的健康状态。

Usage:
    python3 system_health_check.py          # 标准输出
    python3 system_health_check.py --quiet  # 只输出异常
    python3 system_health_check.py --json   # JSON 输出

Exit codes:
    0 = ✅ 全部正常
    1 = ⚠️ 有警告但不影响运行
    2 = ❌ 有错误（需要修复）
"""
import json
import os
import subprocess
import sys
import csv
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path(os.environ.get(
    'HERMES_PROFILE_DIR',
    Path.home() / '.hermes' / 'profiles' / 'ai-investor'
))
SCRIPTS = BASE / 'scripts'
DATA = BASE / 'data'
ENGINE_DIR = Path('/mnt/e/AIGC-KB/输出/investment-engine')
KMS_DIR = Path('/mnt/e/AIGC-KB/kms-engine')

# 每个检查返回 (name, ok:bool, message:str)
checks = []


def check(ok: bool, msg: str):
    checks.append(checks[-1])  # placeholder, we'll use explicit


def add(name: str, ok: bool, msg: str, level: str = "error"):
    checks.append({"name": name, "ok": ok, "msg": msg, "level": level})


# ── 1. 核心文件完整性 ──
CORE_SCRIPTS = [
    "tri_role.py", "quality_gate.py", "session_metrics.py",
    "crystallize_to_skill.py", "pre_plan_validator.py",
    "model_tier_router.py", "cost_budget.py",
    "context_router.py", "compress_context.py",
    "skill_benchmark.py", "skill_trainer.py",
]
HARNESS_FILES = [
    ENGINE_DIR / "strategies" / "pipeline_sentinel.py",
    ENGINE_DIR / "strategies" / "state_manager.py",
    ENGINE_DIR / "strategies" / "governance_guard.py",
    KMS_DIR / "scripts" / "checkpoint_utils.py",
    KMS_DIR / "scripts" / "market_daily_pipeline.py",
    KMS_DIR / "scripts" / "investment_dashboard.py",
]
DATA_FILES = [
    DATA / "trajectory.jsonl",
    DATA / "audit_log.jsonl",
    DATA / "session_metrics.csv",
    KMS_DIR / "config" / "strategy_current.json",
]

for script in CORE_SCRIPTS:
    p = SCRIPTS / script
    exists = p.exists()
    add(f"🔧 {script}", exists, f"{'存在' if exists else '缺失'}: {p.name}",
        "error" if not exists else "ok")

for hp in HARNESS_FILES:
    exists = hp.exists()
    add(f"🔧 {hp.parent.name}/{hp.name}", exists,
        f"{'存在' if exists else '缺失'}: {hp.name}",
        "error" if not exists else "ok")

# ── 2. 编译检查 ──
compile_errors = 0
for script in CORE_SCRIPTS:
    r = subprocess.run([sys.executable, "-m", "py_compile", str(SCRIPTS / script)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        compile_errors += 1
        add(f"📝 {script}", False, f"编译错误: {r.stderr[:80]}", "error")

if compile_errors == 0:
    add("📝 编译检查", True, f"全部 {len(CORE_SCRIPTS)} 个脚本编译通过", "ok")

# ── 3. 数据文件可写检查 ──
for df in DATA_FILES:
    try:
        if not df.parent.exists():
            df.parent.mkdir(parents=True, exist_ok=True)
        df.touch(exist_ok=True)
        add(f"📊 {df.name}", True, f"可写: {df}", "ok")
    except OSError as e:
        add(f"📊 {df.name}", False, f"不可写: {e}", "error")

# ── 4. tri_role 基础功能 ──
r = subprocess.run([sys.executable, str(SCRIPTS / "tri_role.py"), "--list"],
                   capture_output=True, text=True, timeout=10)
if r.returncode == 0 and "final-review" in r.stdout:
    roles = [l.split()[0] for l in r.stdout.splitlines() if l.strip() and l.strip() != "可用角色:"]
    add("🚀 tri_role --list", True, f"可用 {len(roles)} 角色: {', '.join(roles[:4])}...", "ok")
else:
    add("🚀 tri_role --list", False, f"失败: {r.stderr[:100]}", "error")

# ── 5. quality_gate 可运行 ──
r = subprocess.run([sys.executable, str(SCRIPTS / "quality_gate.py"), "--help"],
                   capture_output=True, text=True, timeout=10)
add("🚦 quality_gate", r.returncode == 0,
    "可用" if r.returncode == 0 else f"失败: {r.stderr[:100]}",
    "error" if r.returncode != 0 else "ok")

# ── 6. cost_budget 可运行 ──
r = subprocess.run([sys.executable, str(SCRIPTS / "cost_budget.py"), "status"],
                   capture_output=True, text=True, timeout=10)
if r.returncode in (0, 1):
    # Extract key metrics from output
    lines = r.stdout.splitlines()
    budget_info = [l.strip() for l in lines if "Total" in l or "⚠" in l or "Budget" in l]
    budget_str = "; ".join(budget_info[:2]) if budget_info else "OK"
    add("💰 cost_budget", True, budget_str, "warning" if r.returncode == 1 else "ok")
else:
    add("💰 cost_budget", False, f"失败: {r.stderr[:100]}", "error")

# ── 7. strategy_current.json 有效性 ──
scj = KMS_DIR / "config" / "strategy_current.json"
try:
    data = json.loads(scj.read_text(encoding='utf-8'))
    regime = data.get('regime', {})
    tasks = data.get('tasks', [])
    completed = sum(1 for t in tasks if t.get("status") == "completed")
    pending = sum(1 for t in tasks if t.get("status") == "pending")
    add("📋 strategy_current", True,
        f"市况: {regime.get('label','?')} | 任务: {completed}完成/{pending}待办",
        "warning" if pending > 0 and completed == 0 else "ok")
except (json.JSONDecodeError, OSError) as e:
    add("📋 strategy_current", False, f"解析失败: {e}", "error")

# ── 8. 最近活动检查 ──
today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

# Check trajectory for today
traj_path = DATA / "trajectory.jsonl"
traj_today = 0
if traj_path.exists():
    for line in traj_path.read_text(encoding='utf-8').splitlines():
        if today in line:
            traj_today += 1
add("📈 trajectory 今日活动", traj_today > 0,
    f"{traj_today} 条记录" if traj_today > 0 else "今日无记录",
    "warning" if traj_today == 0 else "ok")

# Check audit for today
audit_path = DATA / "audit_log.jsonl"
audit_today = 0
if audit_path.exists():
    for line in audit_path.read_text(encoding='utf-8').splitlines():
        if today in line:
            audit_today += 1
add("🔍 audit 今日活动", audit_today > 0,
    f"{audit_today} 条记录" if audit_today > 0 else "今日无记录",
    "warning" if audit_today == 0 else "ok")

# ── 9. 测试套件 ──
r = subprocess.run([sys.executable, "-m", "pytest", str(KMS_DIR / "tests"), "-q"],
                   capture_output=True, text=True, timeout=30)
if r.returncode == 0:
    # Extract test count
    import re
    m = re.search(r'(\d+) passed', r.stdout)
    passed = m.group(1) if m else "?"
    add("🧪 pytest", True, f"{passed} passed", "ok")
else:
    failed = r.stdout.splitlines()[-2] if r.stdout.splitlines() else "?"
    add("🧪 pytest", False, f"失败: {failed}", "error")

# ── 10. 成本状态 ──
cost_path = DATA / "cost_tracker.csv"
if cost_path.exists():
    lines = cost_path.read_text(encoding='utf-8').splitlines()
    today_costs = [l for l in lines if today in l]
    add("💰 今日成本", len(today_costs) > 0,
        f"{len(today_costs)} 条记录" if len(today_costs) > 0 else "今日无记录",
        "warning" if len(today_costs) == 0 else "ok")
else:
    add("💰 今日成本", False, "cost_tracker.csv 不存在", "warning")


# ── 报告生成 ──
def report(quiet: bool = False, json_output: bool = False):
    all_ok = all(c['ok'] for c in checks)
    errors = [c for c in checks if not c['ok'] and c['level'] == 'error']
    warnings = [c for c in checks if not c['ok'] and c['level'] == 'warning']
    pending_ok = [c for c in checks if c['ok']]

    if json_output:
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "all_ok": all_ok,
            "total": len(checks),
            "passed": len(pending_ok),
            "errors": len(errors),
            "warnings": len(warnings),
            "checks": checks,
        }, ensure_ascii=False, indent=2))
        return 0 if all_ok else (1 if warnings else 2)

    if not quiet:
        print()
        print("=" * 60)
        print(f"  🏥  Loop 体系健康检查 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        print()

    for c in checks:
        symbol = "✅" if c['ok'] else ("⚠️" if c['level'] == 'warning' else "❌")
        if not quiet or not c['ok']:
            print(f"  {symbol} {c['name']}: {c['msg']}")

    print()
    if not quiet:
        print(f"  总计: ✅ {len(pending_ok)} 通过 / ⚠️ {len(warnings)} 警告 / ❌ {len(errors)} 错误")
        print()
        if all_ok:
            print("  🎉 体系健康，全部正常！")
        elif errors:
            print(f"  ❌ 有 {len(errors)} 个错误需要修复")
        elif warnings:
            print(f"  ⚠️ 有 {len(warnings)} 个警告，不影响运行")

    return 0 if all_ok else (1 if warnings else 2)


# ── Snapshot / Trend ──
HEALTH_TREND_FILE = DATA / "health_trend.csv"
TREND_HEADER = ['date', 'total', 'passed', 'errors', 'warnings', 'score_pct', 'note']


def cmd_snapshot():
    """记录今日健康快照到 trend CSV"""
    DATA.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    all_ok = all(c['ok'] for c in checks)
    errors_n = sum(1 for c in checks if not c['ok'] and c['level'] == 'error')
    warnings_n = sum(1 for c in checks if not c['ok'] and c['level'] == 'warning')
    passed_n = sum(1 for c in checks if c['ok'])
    total_n = len(checks)
    score = round(passed_n / total_n * 100, 1) if total_n > 0 else 0

    trend_exists = HEALTH_TREND_FILE.exists()
    with open(HEALTH_TREND_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not trend_exists:
            w.writerow(TREND_HEADER)
        w.writerow([today, total_n, passed_n, errors_n, warnings_n, score,
                    "PASS" if all_ok else ("WARN" if warnings_n else "FAIL")])
    print(f"📸 健康快照已记录: {today} — {score}% ({passed_n}/{total_n})")


def cmd_trend(days: int = 7):
    """显示最近 N 天的健康趋势"""
    DATA.mkdir(parents=True, exist_ok=True)
    if not HEALTH_TREND_FILE.exists():
        print("⚠️ 暂无历史趋势数据")
        return 0

    rows = []
    with open(HEALTH_TREND_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("⚠️ 暂无历史趋势数据")
        return 0

    recent = rows[-days:]
    print()
    print("=" * 60)
    print(f"  📈 健康趋势 — 最近 {min(days, len(recent))} 天")
    print("=" * 60)
    print()
    print(f"  {'日期':<12} {'通过':<8} {'错误':<8} {'警告':<8} {'得分':<8} {'状态'}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
    for r in recent:
        score = float(r.get('score_pct', 0))
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        print(f"  {r.get('date','?'):<12} {r.get('passed','?'):<8} {r.get('errors','?'):<8} "
              f"{r.get('warnings','?'):<8} {score:<6.1f}% {bar}")
    print()

    # 趋势方向判断
    if len(recent) >= 3:
        scores = [float(r.get('score_pct', 0)) for r in recent[-3:]]
        if scores[-1] > scores[0]:
            print(f"  📈 趋势: 上升 (+{scores[-1] - scores[0]:.1f}分)")
        elif scores[-1] < scores[0]:
            print(f"  📉 趋势: 下降 ({scores[-1] - scores[0]:+.1f}分) ⚠️")
        else:
            print(f"  ➡️ 趋势: 持平")
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Loop 体系健康检查')
    parser.add_argument('--quiet', action='store_true', help='只输出异常')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    parser.add_argument('--snapshot', action='store_true', help='记录今日健康快照')
    parser.add_argument('--trend', type=int, nargs='?', const=7, default=0,
                        help='显示最近 N 天趋势 (默认7天)')
    args = parser.parse_args()

    if args.snapshot:
        return cmd_snapshot()
    if args.trend:
        return cmd_trend(args.trend)

    quiet = args.quiet
    json_out = args.json
    return report(quiet=quiet, json_output=json_out)


# Wrap existing __main__ logic
if __name__ == '__main__':
    sys.exit(main())
