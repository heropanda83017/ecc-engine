#!/usr/bin/env python3
"""
ecc_benchmark.py — ECC 效果基准测试

衡量 ECC 审查管线对代码质量的影响，对比「有 ECC」vs「无 ECC」
在相同任务上的表现。

测试指标:
  - 代码精简率（与 Ponytail 类似）
  - 审查通过率（首次 APPROVED 率）
  - 审查周期时间
  - 问题发现率（每个审查发现的问题数）

用法:
    python3 ecc_benchmark.py              # 完整运行
    python3 ecc_benchmark.py --quick      # 快速模式（2轮）
    python3 ecc_benchmark.py --report     # 仅输出上次结果
"""

import json, os, sys, time, subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from ecc import config
BASE = config._ECC_HOME
SCRIPTS = BASE / "ecc" / "core"
RESULTS_FILE = config.BENCHMARK_FILE

# ── 测试任务 ──
# 每个任务是一个代码审查请求，测量 ECC 审查的效果
BENCHMARK_TASKS = [
    {
        "name": "email-validator",
        "prompt": "Write a Python function that validates email addresses",
        "expected_issues": ["stdlib: re/email-validator exists"]
    },
    {
        "name": "debounce",
        "prompt": "Write a reusable debounce function in vanilla JavaScript",
        "expected_issues": ["no framework needed"]
    },
    {
        "name": "csv-sum",
        "prompt": "Write Python code that reads sales.csv and sums the 'amount' column",
        "expected_issues": ["stdlib: csv module", "one-liner possible"]
    },
    {
        "name": "rate-limiter",
        "prompt": "Add rate limiting to a FastAPI endpoint",
        "expected_issues": ["stdlib: slowapi exists"]
    },
]


class ECCBenchmark:
    """ECC vs Non-ECC 对比基准测试"""

    def __init__(self):
        self.results = self._load_results()

    def _load_results(self) -> dict:
        if RESULTS_FILE.exists():
            try:
                return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"runs": [], "summary": {}}

    def _save_results(self):
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._compute_summary()
        RESULTS_FILE.write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _compute_summary(self):
        runs = self.results.get("runs", [])
        if not runs:
            return

        by_mode = defaultdict(list)
        for r in runs:
            by_mode[r.get("mode", "unknown")].append(r)

        summary = {}
        for mode, mode_runs in by_mode.items():
            total = len(mode_runs)
            approved = sum(1 for r in mode_runs if r.get("verdict") == "approved")
            durations = [r.get("duration_s", 0) for r in mode_runs]
            issues = [r.get("issues_count", 0) for r in mode_runs]

            summary[mode] = {
                "total_tasks": total,
                "approved": approved,
                "approval_rate": round(approved / total * 100, 1) if total > 0 else 0,
                "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
                "avg_issues": round(sum(issues) / len(issues), 1) if issues else 0,
            }

        self.results["summary"] = summary
        self.results["last_run"] = datetime.now().isoformat()

    def run_single(self, task: dict, mode: str = "ecc", timeout: int = 60) -> dict:
        """运行单个任务"""
        start = time.time()
        prompt = task["prompt"]

        if mode == "ecc":
            # ECC 模式：通过 FINAL-REVIEW 审查
            cmd = [
                sys.executable, str(SCRIPTS / "tri_role.py"),
                "final-review", "--fast",
                prompt
            ]
        elif mode == "baseline":
            # 无 ECC：直接让模型回答，不加约束
            cmd = [
                "claude", "--bare", "--model", "haiku", "-p",
                f"Reply with only APPROVED or FAIL: {prompt}"
            ]
        else:
            # 有 Ponytail 模式：加懒人约束
            cmd = [
                sys.executable, str(SCRIPTS / "tri_role.py"),
                "final-review", "--fast", "--mode", "ultra",
                prompt
            ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            elapsed = time.time() - start
            output = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            output = "TIMEOUT"

        # 提取审查判决
        verdict = "fail"
        issues_count = 0
        output_upper = output.upper()
        if "APPROVED" in output_upper:
            verdict = "approved"
        elif "CONDITIONAL" in output_upper:
            verdict = "conditional"

        # 估算问题数
        if verdict in ("conditional", "fail"):
            import re
            issues_count = len(re.findall(r'[🔴🟡]', output))

        return {
            "task": task["name"],
            "mode": mode,
            "verdict": verdict,
            "duration_s": round(elapsed, 1),
            "issues_count": issues_count,
            "output_length": len(output),
            "output_preview": output[:200],
        }

    def run(self, modes: list = None, tasks: list = None, quick: bool = False):
        """运行完整基准测试"""
        if modes is None:
            modes = ["baseline", "ecc", "ponytail"]
        if tasks is None:
            tasks = BENCHMARK_TASKS
        if quick:
            tasks = tasks[:2]

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"🏁 ECC 基准测试 #{run_id}")
        print(f"   任务数: {len(tasks)} | 模式数: {len(modes)}")
        print()

        for mode in modes:
            print(f"── 模式: {mode} ──")
            for task in tasks:
                print(f"  测试: {task['name']}... ", end="", flush=True)
                result = self.run_single(task, mode)
                self.results["runs"].append({
                    **result,
                    "run_id": run_id,
                    "timestamp": datetime.now().isoformat(),
                })
                icon = "✅" if result["verdict"] == "approved" else "⚠️"
                print(f"{icon} {result['verdict']} ({result['duration_s']}s, {result['issues_count']} issues)")
            print()

        self._save_results()

        # 输出对比
        print("═══ 对比结果 ═══")
        summary = self.results["summary"]
        print(f"\n{'模式':<12} {'通过率':<10} {'平均耗时':<12} {'平均问题':<10}")
        print("-" * 44)
        for mode in modes:
            s = summary.get(mode, {})
            print(f"{mode:<12} {s.get('approval_rate', 0):>6.1f}%   {s.get('avg_duration_s', 0):>6.1f}s   {s.get('avg_issues', 0):>6.1f}")

    def report(self):
        """输出上次测试报告"""
        summary = self.results.get("summary", {})
        if not summary:
            print("❌ 尚无测试结果。运行: python3 ecc_benchmark.py")
            return

        last_run = self.results.get("last_run", "unknown")
        total_runs = len(self.results.get("runs", []))

        print(f"📊 ECC 基准测试报告")
        print(f"   最后运行: {last_run}")
        print(f"   总测试数: {total_runs}")
        print()
        print(f"{'模式':<12} {'通过率':<10} {'平均耗时':<12} {'平均问题':<10}")
        print("-" * 44)
        for mode, s in sorted(summary.items()):
            print(f"{mode:<12} {s.get('approval_rate', 0):>6.1f}%   {s.get('avg_duration_s', 0):>6.1f}s   {s.get('avg_issues', 0):>6.1f}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ECC 基准测试")
    parser.add_argument("--quick", action="store_true", help="快速模式（2轮）")
    parser.add_argument("--report", action="store_true", help="仅输出上次结果")
    args = parser.parse_args()

    bm = ECCBenchmark()

    if args.report:
        bm.report()
    else:
        modes = ["baseline", "ecc"] if args.quick else ["baseline", "ecc", "ponytail"]
        bm.run(modes=modes, quick=args.quick)
