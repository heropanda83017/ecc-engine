#!/usr/bin/env python3
"""
model_router.py — 多模型路由与审查集成

当主模型（deepseek-v4-pro）超时或失败时，自动切换到备选模型进行审查，
并支持多模型集成投票（ensemble verdict aggregation）。

用法:
    from model_router import ensemble_review, ModelRouter

    # 简单路由
    router = ModelRouter()
    result = router.call("final-review", context, timeout=120)

    # 多模型集成
    results = ensemble_review("final-review", context)
    # → {"verdicts": [...], "aggregated": "approved", "details": {...}}
"""

import os, sys, subprocess, json, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ecc import config
BASE = config._ECC_HOME

# ── 模型定义 ──
# 按优先级排列，每级包含模型命令和超时时间
MODEL_TIERS = [
    {
        "name": "deepseek-v4-pro",
        "cmd": ["claude", "--bare", "--model", "opus"],
        "timeout": 300,
        "weight": 3,  # 投票权重
    },
    {
        "name": "glm-5.1",
        "cmd": ["claude", "--bare", "--model", "haiku"],
        "timeout": 120,
        "weight": 2,
    },
]


def parse_verdict(output: str) -> str:
    """解析模型输出的判决结果，支持多种输出格式"""
    if not output:
        return "fail"
    # 检查第一行是否包含判决关键词
    first_line = output.strip().split('\n')[0].upper()
    if "APPROVED" in first_line:
        return "approved"
    if "CONDITIONAL" in first_line:
        return "conditional"
    # 全文搜索判决关键词
    full_upper = output.upper()
    if re.search(r'\bAPPROVED\b', full_upper):
        return "approved"
    if re.search(r'\bCONDITIONAL\b', full_upper):
        return "conditional"
    # Check for FAIL/REQUEST_CHANGES/REJECT as fail signals
    if re.search(r'\b(FAIL|REQUEST_CHANGES|REJECT)\b', full_upper):
        return "fail"
    # 默认：找不到明确判决视为 fail
    return "fail"


def call_model(model_cfg: dict, role: str, prompt: str,
               timeout: int = 120, trace: bool = True) -> dict:
    """调用单个模型并返回结果"""
    start = datetime.now()
    cmd = model_cfg["cmd"] + ["-p", prompt]
    t = timeout or model_cfg.get("timeout", 120)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=t
        )
        elapsed = (datetime.now() - start).total_seconds()
        verdict = parse_verdict(result.stdout)

        out = {
            "model": model_cfg["name"],
            "verdict": verdict,
            "elapsed_s": round(elapsed, 1),
            "exit_code": result.returncode,
            "output": result.stdout[:2000],
            "error": result.stderr[:500] if result.stderr else None,
        }

        # 记录到 traces（如果 tracer 可用）
        if trace:
            try:
                _record_trace(out, role)
            except Exception:
                pass

        return out

    except subprocess.TimeoutExpired:
        elapsed = (datetime.now() - start).total_seconds()
        return {
            "model": model_cfg["name"],
            "verdict": "timeout",
            "elapsed_s": round(elapsed, 1),
            "exit_code": -1,
            "output": "",
            "error": f"Timeout after {t}s",
        }
    except Exception as e:
        return {
            "model": model_cfg["name"],
            "verdict": "error",
            "elapsed_s": 0,
            "exit_code": -1,
            "output": "",
            "error": str(e),
        }


def _record_trace(result: dict, role: str):
    """写入 traces_v2.jsonl"""
    traces_file = BASE / "data" / "traces_v2.jsonl"
    try:
        traces_file.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        entry = {
            "trace_id": uuid.uuid4().hex[:16],
            "span_id": uuid.uuid4().hex[:16],
            "parent_span_id": None,
            "name": f"model_router:{role}:{result['model']}",
            "start_time": 0,
            "end_time": 0,
            "duration_ns": int(result["elapsed_s"] * 1e9),
            "attributes": {
                "model": result["model"],
                "verdict": result["verdict"],
                "role": role,
            },
            "events": [],
            "status": "OK" if result["verdict"] in ("approved", "conditional") else "ERROR",
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(traces_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def aggregate_verdicts(results: list[dict]) -> str:
    """
    多模型投票聚合：
    - 加权投票（每个模型有权重）
    - approved > conditional > fail
    - 平局时选最安全的（conditional）
    """
    if not results:
        return "fail"

    scores = {"approved": 0, "conditional": 0, "fail": 0}
    total_weight = 0

    for r in results:
        v = r["verdict"]
        if v in scores:
            # 根据模型定义查找权重
            w = 1
            for tier in MODEL_TIERS:
                if tier["name"] == r.get("model", ""):
                    w = tier["weight"]
                    break
            scores[v] += w
            total_weight += w

    if total_weight == 0:
        return "conditional"

    # 加权分数排序
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])

    # 如果最高分有绝对优势（>=50%权重），取该判决
    top_verdict, top_score = sorted_scores[0]
    if top_score / total_weight >= 0.5:
        return top_verdict

    # 否则保守取 conditional
    return "conditional"


class ModelRouter:
    """模型路由：按优先级尝试模型，返回第一个成功结果"""

    def __init__(self):
        self.prompt_cache = {}

    def build_prompt(self, role: str, context: str) -> str:
        """构建完整的审查 prompt（加载角色模板 + rules + 上下文）"""
        cache_key = (role, context[:100])
        if cache_key in self.prompt_cache:
            return self.prompt_cache[cache_key]

        templates_dir = config.TEMPLATES_DIR
        rules_dir = config.RULES_DIR

        parts = []

        # 1. 角色模板 — 支持带连接符的文件名
        role_file = role.upper()
        tmpl_file = templates_dir / f"{role_file}.md"
        if not tmpl_file.exists():
            # 尝试连字符版本（如 final-review → FINAL-REVIEW.md）
            tmpl_file = templates_dir / f"{role_file.replace('_', '-')}.md"
        if not tmpl_file.exists():
            # 尝试下划线版本
            tmpl_file = templates_dir / f"{role_file.replace('-', '_')}.md"
        if tmpl_file.exists():
            parts.append(tmpl_file.read_text(encoding="utf-8"))
            parts.append("")

        # 2. rules 约束
        if rules_dir.exists():
            rules_files = sorted(rules_dir.glob("*.md"))
            if rules_files:
                parts.append("## 始终遵循的规则")
                for rf in rules_files:
                    content = rf.read_text(encoding="utf-8")
                    title = ""
                    for line in content.split("\n"):
                        if line.startswith("#"):
                            title = line.replace("#", "").strip()
                            break
                    body_lines = [l.strip() for l in content.split("\n")
                                  if l.strip() and not l.startswith("#") and not l.startswith("```")]
                    first_rule = body_lines[0] if body_lines else ""
                    if title:
                        parts.append(f"- **{title}**: {first_rule}")
                parts.append("")

        # 3. 用户上下文
        if context:
            parts.append("## 任务上下文")
            parts.append(context)

        full_prompt = "\n".join(parts)
        self.prompt_cache[cache_key] = full_prompt
        return full_prompt

    def call(self, role: str, context: str,
             timeout: int = None, fallback: bool = True) -> dict:
        """
        按优先级调用模型。
        如果主模型失败且 fallback=True，自动尝试下一级。
        返回第一个有效结果。
        """
        prompt = self.build_prompt(role, context)

        for tier in MODEL_TIERS:
            result = call_model(tier, role, prompt, timeout=timeout)
            print(f"  model_router: {tier['name']} → {result['verdict']} ({result['elapsed_s']}s)",
                  file=sys.stderr)

            if result["verdict"] in ("approved", "conditional"):
                return result

            # 如果成功但只是 conditional，返回让调用者决定
            if result["verdict"] == "conditional":
                return result

            if not fallback:
                break

            print(f"  model_router: {tier['name']} failed, trying next tier...",
                  file=sys.stderr)

        # 全部失败
        return {
            "model": "all-failed",
            "verdict": "fail",
            "elapsed_s": 0,
            "exit_code": -1,
            "output": "",
            "error": "所有模型调用均失败",
        }


def ensemble_review(role: str, context: str,
                    models: list[str] = None,
                    timeout: int = 120) -> dict:
    """
    多模型集成审查：同时调用多个模型，聚合判决。
    返回聚合结果和每个模型的详细信息。
    """
    selected_tiers = MODEL_TIERS
    if models:
        selected_tiers = [t for t in MODEL_TIERS if t["name"] in models]
        if not selected_tiers:
            selected_tiers = [MODEL_TIERS[0]]

    prompt = ModelRouter().build_prompt(role, context)
    results = []

    print(f"  ensemble_review: 启动 {len(selected_tiers)} 个模型并行审查...",
          file=sys.stderr)

    with ThreadPoolExecutor(max_workers=len(selected_tiers)) as executor:
        futures = {
            executor.submit(call_model, tier, role, prompt, timeout=timeout): tier
            for tier in selected_tiers
        }
        for future in as_completed(futures):
            tier = futures[future]
            try:
                result = future.result()
                results.append(result)
                icon = {"approved": "✅", "conditional": "⚠️",
                        "fail": "❌", "timeout": "⏰", "error": "💥"}
                print(f"    {icon.get(result['verdict'], '❓')} {tier['name']}: "
                      f"{result['verdict']} ({result['elapsed_s']}s)",
                      file=sys.stderr)
            except Exception as e:
                print(f"    ❌ {tier['name']}: {e}", file=sys.stderr)
                results.append({
                    "model": tier["name"],
                    "verdict": "error",
                    "elapsed_s": 0,
                    "exit_code": -1,
                    "output": "",
                    "error": str(e),
                })

    aggregated = aggregate_verdicts(results)

    # 按模型名排序输出
    results.sort(key=lambda r: r.get("model", ""))

    return {
        "verdicts": results,
        "aggregated": aggregated,
        "total_time_s": round(sum(r["elapsed_s"] for r in results), 1),
        "parallel_time_s": round(max(r["elapsed_s"] for r in results), 1),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="多模型路由与审查")
    parser.add_argument("role", help="审查角色 (final-review, review, spec-reviewer)")
    parser.add_argument("context", help="审查上下文")  # we'll accept from stdin too
    parser.add_argument("--mode", choices=["single", "ensemble"], default="single",
                        help="single=顺序降级, ensemble=并行投票")
    parser.add_argument("--timeout", type=int, default=120, help="每模型超时(秒)")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    context = args.context
    if not context and not sys.stdin.isatty():
        context = sys.stdin.read().strip()

    if not context:
        print("❌ 请提供审查上下文（参数或 stdin）", file=sys.stderr)
        sys.exit(1)

    if args.mode == "ensemble":
        result = ensemble_review(args.role, context, timeout=args.timeout)
    else:
        router = ModelRouter()
        result = router.call(args.role, context, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.mode == "ensemble":
            print(f"\n## 集成审查结果 — {args.role}")
            print(f"\n**聚合判决: {result['aggregated'].upper()}**")
            print(f"\n| 模型 | 判决 | 耗时(s) |")
            print(f"|:-----|:----:|:-------:|")
            for r in result["verdicts"]:
                icon = {"approved": "✅", "conditional": "⚠️", "fail": "❌",
                        "timeout": "⏰", "error": "💥"}
                print(f"| {icon.get(r['verdict'], '❓')} {r['model']} | {r['verdict']} | {r['elapsed_s']} |")
            print(f"\n**总耗时: {result['total_time_s']}s (并行: {result['parallel_time_s']}s)**")
        else:
            print(f"\n{args.role.upper()} — {result['verdict']}")
            print(f"模型: {result.get('model', '?')} | 耗时: {result.get('elapsed_s', 0)}s")
            if result.get("error"):
                print(f"错误: {result['error']}")
            if result.get("output"):
                print(f"\n输出:\n{result['output'][:500]}")

    exit_code = {"approved": 0, "conditional": 1, "fail": 2}
    sys.exit(exit_code.get(result.get("aggregated") if args.mode == "ensemble" else result.get("verdict"), 2))
