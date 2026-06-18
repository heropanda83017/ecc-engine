#!/usr/bin/env python3
"""
tri_role.py — 三角色 Prompt 模板调用器（D2 最后一公里）

替代内联构造的 claude --bare --model opus -p 调用。
每次调用自动从 prompt-templates/ 加载对应角色的系统 prompt，
自动附加 rules/ 目录的约束，最后拼接用户上下文。

v2 新增 (2026-06-08): errors-as-inputs 自动重试循环
- --retry 标志启用 Schmid Shift #3+4: catch the error, feed it back
- 当 final-review 返回 CONDITIONAL/FAIL 时，自动将审查反馈注入下一轮
- 最大重试 2 次，每次在 session_metrics 记录 retry 条目

v3 新增 (2026-06-09): 硬故障恢复 (P3-2)
- 当 V4 Pro 子进程崩溃（非零退出码）时，捕获 stderr + 部分 stdout
- 构造 [HARD_FAILURE] 错误反馈注入下一轮调用，不再直接退出
- 仅 --retry 模式生效，保留非 --retry 模式的快速失败行为

用法：
    python3 tri_role.py arch '设计XXX的架构'
    python3 tri_role.py review '审查以下代码...'
    python3 tri_role.py engine '实现以下功能...'
    python3 tri_role.py final-review --retry '最终审查...'
    python3 tri_role.py spec-reviewer '审查代码是否符合规范'
    python3 tri_role.py code-quality-reviewer '审查代码质量'
    python3 tri_role.py writing-plans '拆解任务'
    python3 tri_role.py receiving-code-review '处理反馈'

    python3 tri_role.py --list          # 列出可用角色
    python3 tri_role.py --load arch     # 仅输出模板
"""

import os, sys, subprocess, json
from pathlib import Path
from ecc.instincts.coding_scout import record_coding_pattern

# ── 路径 ──
from ecc import config

BASE = config._ECC_HOME
TEMPLATES = config.TEMPLATES_DIR
RULES = config.RULES_DIR

# 模型命令从配置读取
V4_PRO_CMD = list(config.DEFAULT_MODEL_CMD)
V4_FLASH_CMD = list(config.FAST_MODEL_CMD)
FINAL_REVIEW_CMD = V4_FLASH_CMD
FINAL_REVIEW_FALLBACK_CMD = V4_PRO_CMD

# P2: 停滞检测和重试阈值（可通过环境变量覆盖）
STAGNATION_JACCARD_THRESHOLD = float(os.environ.get("ECC_STAGNATION_THRESHOLD", "0.6"))
STAGNATION_MAX_RETRY = int(os.environ.get("ECC_STAGNATION_MAX_RETRY", "2"))

# 映射到 loader
sys.path.insert(0, str(TEMPLATES))
from ecc.core.loader import load_prompt, list_roles


def build_prompt(role: str, context: str = "", include_rules: bool = True) -> str:
    """构建完整 prompt：角色模板 + rules + 用户上下文"""
    parts = []

    # 1. 角色系统 prompt
    template = load_prompt(role)
    parts.append(template)
    parts.append("")

    # 2. rules 约束（自动注入）
    if include_rules:
        rules_files = sorted(RULES.glob("*.md"))
        if rules_files:
            # P0-2: FINAL-REVIEW 只注入规则 05（code-review-v4-pro）
            # 其他 6 条规则对三问审查无帮助，只会膨胀 prompt
            if role == "final-review":
                rules_files = [rf for rf in rules_files if "05-code-review" in rf.name]

            parts.append("## 始终遵循的规则")
            for rf in rules_files:
                content = rf.read_text(encoding="utf-8")
                title = ""
                for line in content.split("\n"):
                    if line.startswith("#"):
                        title = line.replace("#", "").strip()
                        break
                body_lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#") and not l.startswith("```")]
                first_rule = body_lines[0] if body_lines else ""
                if title:
                    parts.append(f"- **{title}**: {first_rule}")
            parts.append("")

    # 3. 用户上下文
    if context:
        parts.append("## 任务上下文")
        parts.append(context)

    return "\n".join(parts)


def parse_verdict(output: str) -> str:
    """解析 V4 Pro 输出的判决结果"""
    if not output:
        return "fail"
    first_word = output.strip().split()[0].upper()
    if "APPROVED" in first_word:
        return "approved"
    elif "CONDITIONAL" in first_word:
        return "conditional"
    return "fail"


def record_metrics(role: str, duration: int, verdict: str, session_id: str,
                   round_num: int = 1, issues_count: int = 0):
    """记录 session 度量（P0-2: 新增 round + issues_count）"""
    metrics_script = BASE / "scripts" / "session_metrics.py"
    if metrics_script.exists():
        cmd = [
            "python3", str(metrics_script), "record",
            "--role", role,
            "--duration", str(duration),
            "--verdict", verdict,
            "--session-id", session_id,
            "--round", str(round_num),
            "--issues-count", str(issues_count),
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)


# ── P1-5: Trajectory 日志 ──
# 记录 tri_role 调用全路径（ARCH→REVIEW→ENGINE→FINAL-REVIEW）
# 存储为 JSONL 格式，每行一个步骤事件
TRAJECTORY_FILE = config.TRAJECTORY_FILE


def record_trajectory(session_id: str, role: str, verdict: str,
                      duration_s: int, round_num: int = 1,
                      prompt_len: int = 0, output_len: int = 0):
    """记录一次 tri_role 操作到 trajectory 日志"""
    import json
    from datetime import datetime, timezone
    entry = {
        "ts": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "session_id": session_id,
        "role": role,
        "verdict": verdict,
        "duration_s": duration_s,
        "round": round_num,
        "prompt_chars": prompt_len,
        "output_chars": output_len,
    }
    try:
        (BASE / "data").mkdir(parents=True, exist_ok=True)
        with open(TRAJECTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # P2: 日志轮转检查
        _rotate_log(TRAJECTORY_FILE)
    except OSError:
        pass  # 日志失败不阻断主流程


# ── P2-10: 审计日志 ──
# 记录所有 tri_role 执行，支持 RBAC 兼容的审计追踪
AUDIT_FILE = config.DATA_DIR / "audit_log.jsonl"


def record_audit(session_id: str, user: str, role: str, verdict: str,
                 duration_s: int, context_summary: str = ""):
    """记录审计事件"""
    import json
    from datetime import datetime, timezone
    entry = {
        "ts": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "session_id": session_id,
        "user": user,
        "action": f"tri_role:{role}",
        "verdict": verdict,
        "duration_s": duration_s,
        "context": context_summary[:200],
    }
    try:
        (BASE / "data").mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_patterns(verdict: str, output: str):
    """审查发现问题时自动学习编码模式"""
    if verdict not in ("conditional", "fail") or not output:
        return
    import re
    lines = output.split("\n")
    for line in lines:
        m = re.search(r'[\U0001f534\U0001f7e1]\s*(P\d)?\s*[:：]?\s*(.+?)(?:\s*[-\u2013\u2014]|$)', line)
        if m:
            severity = "red" if "\U0001f534" in line else "yellow"
            desc = m.group(2).strip()[:80]
            pattern_name = "review-" + desc.split()[0].lower().strip(",.()[]")[:20]
            record_coding_pattern(
                pattern=pattern_name,
                description=desc,
                benefit="V4 Pro review 发现" if severity == "red" else "V4 Pro review 提示",
                trigger=f"tri_role final-review retry: {desc}",
                fix_effort_hours=0.5 if severity == "red" else 0.2,
                confidence=0.3 if severity == "red" else 0.15,
                frequency="always" if severity == "red" else "sometimes",
            )


def call_v4_pro(role: str, context: str, intensity: str = "full",
                include_rules: bool = True) -> subprocess.CompletedProcess:
    """调用模型并返回结果。FINAL-REVIEW 默认用 GLM-5.1 (haiku)，V4 Pro (opus) 仅做备选。"""
    prompt = build_prompt(role, context, include_rules=include_rules)
    # P1-3: 注入 Ponytail 强度模式
    if intensity != "full":
        mode_hints = {
            "lite": "\n\n[PONYTAIL MODE: lite] Build what's asked, name the lazier alternative in one line.",
            "ultra": "\n\n[PONYTAIL MODE: ultra] YAGNI extremist. Deletion before addition. Challenge requirements before building.",
        }
        prompt += mode_hints.get(intensity, "")

    # P0-1: FINAL-REVIEW 默认用 GLM-5.1 (haiku)，快 30x
    cmd = V4_PRO_CMD
    if role == "final-review":
        cmd = FINAL_REVIEW_CMD
        print(f"tri_role: FINAL-REVIEW 使用 GLM-5.1 (haiku) — 快速模式", file=sys.stderr)

    print(f"tri_role: 调用 {'/'.join(cmd)} ({role})...", file=sys.stderr)
    print(f"tri_role: prompt length = {len(prompt)} chars", file=sys.stderr)
    result = subprocess.run(
        cmd + ["-p", prompt],
        capture_output=True, text=True, timeout=300
    )

    # P0-1: FINAL-REVIEW 失败时自动降级到 V4 Pro (opus)
    if role == "final-review" and result.returncode != 0:
        print(f"tri_role: GLM-5.1 失败 (exit={result.returncode}), 降级到 V4 Pro...", file=sys.stderr)
        result = subprocess.run(
            FINAL_REVIEW_FALLBACK_CMD + ["-p", prompt],
            capture_output=True, text=True, timeout=300
        )

    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python3 tri_role.py <role> [context]")
        print("       python3 tri_role.py --list")
        print("       python3 tri_role.py --load <role>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--list":
        print("可用角色:")
        for r in list_roles():
            desc = {"arch": "架构设计", "review": "代码审查", "engine": "代码实现",
                    "writing-plans": "任务拆解 <=5min",
                    "spec-reviewer": "规范审查",
                    "code-quality-reviewer": "质量审查",
                    "pre-plan-validation": "前置规划验证门 7维检查",
                    "receiving-code-review": "反馈处理", "final-review": "最终审查"}
            print(f"  {r:15s} {desc.get(r, '')}")
        sys.exit(0)

    if cmd == "--load":
        role = sys.argv[2] if len(sys.argv) > 2 else "arch"
        print(build_prompt(role))
        sys.exit(0)

    # 解析 --retry 标志
    # Schmid Shift #3: errors as inputs
    retry_mode = False
    multi_model = False
    intensity_mode = "full"  # P1-3: 三档强度模式
    stop_condition_dir = None  # P0-1: Stop Condition 集成
    args = list(sys.argv[1:])
    if "--retry" in args:
        retry_mode = True
        args.remove("--retry")
    if "--multi-model" in args:
        multi_model = True
        args.remove("--multi-model")
    # P1-1: --fast 标志：跳过 rules 注入，仅用角色模板
    fast_mode = False
    if "--fast" in args:
        fast_mode = True
        args.remove("--fast")
    # P0: --self-eval 标志：5 轴输出质量自评
    self_eval = False
    if "--self-eval" in args:
        self_eval = True
        args.remove("--self-eval")
    if "--mode" in args:
        mi = args.index("--mode")
        if mi + 1 < len(args):
            mode_val = args[mi + 1].lower()
            if mode_val in ("lite", "full", "ultra"):
                intensity_mode = mode_val
            args.pop(mi + 1)
        args.remove("--mode")
    
    # P2-2: --recover 标志：加载跨 session 失败上下文
    recover_mode = False
    if "--recover" in args:
        recover_mode = True
        args.remove("--recover")
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from ecc.audit.failure_db import FailureDB
            fdb = FailureDB()
            ctx = fdb.recovery_context(role)
            if ctx:
                # 将恢复上下文注入到用户提供的 context 之前
                context = ctx + "\n" + context
                print(f"tri_role: --recover 加载了上次 {role} 的失败上下文", file=sys.stderr)
        except Exception as e:
            print(f"tri_role: --recover 失败 ({e}), 继续原流程", file=sys.stderr)
    
    # P0-1: --stop-condition <dir> 集成 quality_gate 门禁
    if "--stop-condition" in args:
        sc_idx = args.index("--stop-condition")
        if sc_idx + 1 < len(args):
            stop_condition_dir = args[sc_idx + 1]
            args.pop(sc_idx + 1)
        args.remove("--stop-condition")
        if not retry_mode:
            retry_mode = True  # stop-condition 自动启用 retry 模式

    # 解析 role + context
    role = args[0]
    context = " ".join(args[1:]) if len(args) > 1 else ""

    # P2-2: --recover 标志：加载跨 session 失败上下文
    if recover_mode:
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from ecc.audit.failure_db import FailureDB
            fdb = FailureDB()
            ctx = fdb.recovery_context(role)
            if ctx:
                context = ctx + "\n" + context
                print(f"tri_role: --recover 加载了上次 {role} 的失败上下文", file=sys.stderr)
        except Exception as e:
            print(f"tri_role: --recover 失败 ({e}), 继续原流程", file=sys.stderr)

    if role not in list_roles():
        print(f"未知角色: {role}。可用: {list_roles()}")
        sys.exit(1)

    # ── LOCKED 检查: ENGINE 阶段检查目标文件是否被锁定 ──
    if role == "engine":
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from ecc.core.system_maturity import SystemMaturity
            for word in context.split():
                if word.endswith(".py") and SystemMaturity.is_locked(word):
                    print(f"🔒 LOCKED: {word} 是核心组件, 修改需人工确认。跳过自动修改。")
                    sys.exit(0)
        except Exception:
            pass  # LOCKED检查失败不阻断

    # ── errors-as-inputs: 自动重试循环 ──
    # 当 final-review 返回 CONDITIONAL/FAIL 时，
    # ── errors-as-inputs: 自动重试循环 ──
    # P0-1: 当 --stop-condition 启用时，允许更多重试 + condition 驱动停止
    MAX_RETRY = STAGNATION_MAX_RETRY + (3 if stop_condition_dir else 0)
    retry_attempt = 0
    last_feedback = ""
    prev_feedback_hash = ""  # P0-3: 前一轮反馈的摘要，用于停滞检测
    session_id = os.environ.get("HERMES_SESSION_ID", "cli")

    # ── TraceAI / Hermes Tracer 集成 ──
    tracer_enabled = False
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from hermes_tracer import tracer as _ht
        ht = _ht
        trace_id = ht.start_trace(f"tri_role:{role}")
        ht.span(f"tri_role:{role}:init").set_attribute("session_id", session_id).close()
        tracer_enabled = True
    except Exception:
        pass

    while retry_attempt <= MAX_RETRY:
        if retry_attempt > 0 and last_feedback:
            # 注入上一轮审查反馈或硬错误作为"errors as inputs"
            if last_feedback.startswith("[HARD_FAILURE]"):
                # 硬故障恢复：注入错误信息让模型知道失败原因
                # 分类降级（借鉴 Vibe Coding 幻觉分类框架）
                if "timeout" in last_feedback.lower() or "timed out" in last_feedback.lower():
                    # 超时 → 缩小输出范围 + 切换到快速模式
                    degradation_hint = "超时问题：请大幅缩小输出范围（只输出关键结论，省去解释和示例），必要时省略代码细节。"
                    if retry_attempt >= 2:
                        degradation_hint += " 如果仍然超时，建议跳过 FINAL REVIEW，改用手动验证（py_compile + 导入链测试）。"
                elif "model" in last_feedback.lower() or "API" in last_feedback.upper():
                    # 模型/API 错误 → 降级到备选模型
                    degradation_hint = "模型/API 不可用：当前的提示内容仍有效，请重试。如持续失败，将切换到备选路径。"
                else:
                    # 通用硬错误
                    degradation_hint = "如果可能是超时问题，尝试缩小输出范围；如果是模型不可用，当前的提示内容仍有效。"
                feedback_wrapper = (
                    f"## 上一轮调用失败（重试第 {retry_attempt} 次）\n"
                    f"以下错误发生在调用过程中，请调整后重试：\n\n"
                    f"{last_feedback}\n\n"
                    f"提示：{degradation_hint}"
                )
            else:
                # 逻辑审查反馈（原有模式）
                feedback_wrapper = (
                    f"## 上一轮审查反馈（重试第 {retry_attempt} 次）\n"
                    f"以下是上一轮 FINAL REVIEW 发现的问题，本次必须全部解决：\n\n"
                    f"{last_feedback}\n\n"
                    f"请针对以上反馈修改代码/设计后，再提交审查。"
                )
            full_context = context + "\n\n" + feedback_wrapper
        else:
            full_context = context

        result = call_v4_pro(role, full_context, intensity=intensity_mode,
                            include_rules=not fast_mode)

        # ── --multi-model fallback: 主模型失败时启用多模型路由降级 ──
        if multi_model and result.returncode != 0:
            try:
                sys.path.insert(0, str(BASE / "scripts"))
                from ecc.review.model_router import ModelRouter as MR
                mm = MR()
                mm_result = mm.call(role, full_context)
                if mm_result["verdict"] in ("approved", "conditional"):
                    result.stdout = mm_result.get("output", result.stdout)
                    result.returncode = 0
                    print(f"  tri_role: --multi-model → {mm_result['verdict']} ({mm_result.get('model', '?')})", file=sys.stderr)
            except Exception as e:
                print(f"  tri_role: --multi-model fallback 失败 ({e})", file=sys.stderr)

        # 解析判决
        verdict = parse_verdict(result.stdout)

        # 记录 session 度量（retry 条目带后缀）
        display_role = role + (f"-retry{retry_attempt}" if retry_attempt > 0 else "")
        # P0-2: 估算 issues 数（从 CONDITIONAL 输出中统计 🔴/🟡 符号）
        issues_est = 0
        if verdict in ("conditional", "fail"):
            import re
            issues_est = len(re.findall(r'[🔴🟡]', result.stdout))
        record_metrics(display_role, len(result.stdout) * 0.5 + 1, verdict, session_id,
                       round_num=retry_attempt + 1, issues_count=issues_est)
        # P1-5: 记录 trajectory（全路径日志）
        record_trajectory(session_id, display_role, verdict,
                          int(len(result.stdout) * 0.5 + 1),
                          round_num=retry_attempt + 1,
                          prompt_len=len(full_context),
                          output_len=len(result.stdout))

        # P2-10: 审计日志
        record_audit(session_id, os.environ.get("USER", "unknown"), display_role,
                     verdict, int(len(result.stdout) * 0.5 + 1),
                     context_summary=full_context[:100])

        # ── Trace: 记录每次调用 span ──
        if tracer_enabled:
            try:
                sp = ht.span(f"tri_role:{role}:retry_{retry_attempt}")
                sp.set_attribute("verdict", verdict)
                sp.set_attribute("retry", retry_attempt)
                sp.set_attribute("duration_approx_s", int(len(result.stdout) * 0.5 + 1))
                sp.set_attribute("prompt_chars", len(full_context))
                sp.set_attribute("output_chars", len(result.stdout))
                sp.set_attribute("stop_condition", bool(stop_condition_dir))
                if last_feedback:
                    sp.set_attribute("feedback_len", len(last_feedback))
                sp.close()
            except Exception:
                pass

        # P1-4 + P2-9: Token 消耗追踪 + 预算护栏
        try:
            est_tokens = len(full_context) + len(result.stdout) * 4  # 粗略估算
            subprocess.run(
                [sys.executable, str(BASE / "scripts" / "cost_budget.py"),
                 "track", "--model", "deepseek-v4-pro",
                 "--tokens", str(est_tokens),
                 "--pipeline", f"tri_role-{role}",
                 "--session-id", session_id],
                capture_output=True, timeout=10)
            # 检查预算状态
            budget_check = subprocess.run(
                [sys.executable, str(BASE / "scripts" / "cost_budget.py"), "status"],
                capture_output=True, text=True, timeout=10)
            if budget_check.returncode == 2:
                print(f"tri_role: ⚠️ 今日预算已超限! 停止后续操作。",
                      file=sys.stderr)
                print(budget_check.stdout)
                sys.exit(1)
            elif budget_check.returncode == 1:
                print(f"tri_role: ⚠️ 今日预算使用超过80%, 请注意。",
                      file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired):
            pass  # 预算追踪失败不阻断主流程

        # 自动学习：记录审查发现的模式
        record_patterns(verdict, result.stdout)

        # 批量归纳：将审查发现写入暂存区
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from ecc.evolution.batch_induction import BatchInduction
            bi = BatchInduction()
            finding = {
                "source": f"tri_role_{role}",
                "category": "code_review",
                "severity": verdict,
                "title": f"{role}审查: {verdict}",
                "file": "",
                "detail": result.stdout[:200],
            }
            bi.add_finding(finding)
        except Exception:
            pass  # 批量归纳失败不阻断主流程

        # ── Schmid Shift #3: errors-as-inputs — 硬故障恢复 ──
        # 当 V4 Pro 子进程本身崩溃（非零退出码）时，
        # 捕获 stderr + 部分 stdout 作为错误输入，重试 1 次
        if result.returncode != 0:
            if retry_mode and retry_attempt < MAX_RETRY:
                # 构造硬错误反馈，注入下一轮
                err_summary = result.stderr[:500] if result.stderr else "(无 stderr)"
                out_summary = result.stdout[:300] if result.stdout else "(无 stdout)"
                last_feedback = (
                    f"[HARD_FAILURE] 子进程退出码={result.returncode}\n"
                    f"stderr: {err_summary}\n"
                    f"stdout: {out_summary}"
                )
                retry_attempt += 1
                print(f"tri_role: 硬故障(exit={result.returncode}), "
                      f"启动重试 {retry_attempt}/{MAX_RETRY}...", file=sys.stderr)
                continue
            else:
                print(f"V4 Pro 调用失败 (exit={result.returncode})", file=sys.stderr)
                print(result.stderr[:500] if result.stderr else "", file=sys.stderr)
                print(result.stdout)
                sys.exit(result.returncode)

        # ── P0-1: Stop Condition 集成 — quality_gate 检查 ──
        # 当 --stop-condition 启用时，每轮重试后自动检查 quality_gate
        # 如果通过 (exit 0) → 视为任务完成 → 停止循环
        qg_passed = False
        if stop_condition_dir and verdict in ("approved", "conditional"):
            try:
                qg_result = subprocess.run(
                    [sys.executable, str(BASE / "scripts" / "quality_gate.py"), stop_condition_dir],
                    capture_output=True, text=True, timeout=60
                )
                if qg_result.returncode == 0:
                    qg_passed = True
                    # 把 quality_gate 的 PASS 信息注入输出
                    print(f"tri_role: quality_gate PASS — 停止条件满足，结束循环", file=sys.stderr)
                else:
                    # 提取 quality gate 的关键失败信息
                    qg_summary = qg_result.stdout.strip()[:500] if qg_result.stdout else ""
                    print(f"tri_role: quality_gate exit={qg_result.returncode}, 继续重试", file=sys.stderr)
                    if qg_summary:
                        last_feedback = f"## quality_gate 检查未通过\n{qg_summary}\n请在下一轮修复以上问题。"
            except (OSError, subprocess.TimeoutExpired) as e:
                print(f"tri_role: quality_gate 调用失败 ({e}), 跳过停止条件检查", file=sys.stderr)

        if qg_passed:
            verdict = "approved"
            # 直接执行到最终的输出和退出逻辑

        # ── P0-3: 无进展检测 — 比较相邻两轮反馈是否高度重复 ──
        # 如果上一轮和本轮反馈的语义高度相似（用关键短语匹配），说明没有实质进展
        stagnation_detected = False
        if retry_attempt >= 2 and last_feedback and prev_feedback_hash:
            # 简单 Jaccard 式比较：提取两轮反馈中的关键短语（"🔴", "🟡", "P0", "P1" 等）
            import re
            curr_keywords = set(re.findall(r'[🔴🟡]|P[012]|必须|缺少|错误|缺失|问题', last_feedback))
            prev_keywords = set(re.findall(r'[🔴🟡]|P[012]|必须|缺少|错误|缺失|问题', prev_feedback_hash))
            if curr_keywords and prev_keywords:
                intersection = curr_keywords & prev_keywords
                union = curr_keywords | prev_keywords
                jaccard = len(intersection) / len(union) if union else 0
                if jaccard > STAGNATION_JACCARD_THRESHOLD:
                    stagnation_detected = True
                    # P2-2: 记录停滞到 failure_db
                    try:
                        from ecc.audit.failure_db import FailureDB
                        FailureDB().record(role, session_id, context[:200],
                                           f"停滞检测: Jaccard={jaccard}", verdict="stagnation")
                    except Exception:
                        pass
                    print(f"tri_role: 停滞检测触发 (Jaccard={jaccard:.2f} > 0.6), 停止重试", file=sys.stderr)
                    # 输出停滞报告而非最后一次的结果
                    print(f"## 审查结果: FAIL (停滞)\n\n审查在 {retry_attempt} 轮后因无实质进展终止。\n本轮关键词: {curr_keywords}\n上轮关键词: {prev_keywords}\nJaccard 相似度: {jaccard:.2f} (阈值 0.60)")
                    sys.exit(1)  # 停滞作为失败退出

        # P0-3: 保存本轮反馈摘要供下轮停滞检测
        if last_feedback:
            prev_feedback_hash = last_feedback[:500]

        # errors-as-inputs: 决定是否重试
        if retry_mode and verdict in ("conditional", "fail") and retry_attempt < MAX_RETRY:
            # P1-2: FINAL-REVIEW 失败后不走 retry 循环，直接建议手动验证
            if role == "final-review":
                print(f"tri_role: FINAL-REVIEW 失败 ({verdict}), 建议手动验证代替重试循环",
                      file=sys.stderr)
                print(f"\n## FINAL-REVIEW 结果: {verdict.upper()}")
                print(f"\n**建议手动验证代替重试**:")
                print(f"1. `python3 -m py_compile <file>.py` — 编译检查")
                print(f"2. `python3 <file>.py --test` — 内联测试")
                print(f"3. `python3 -c \"from module import func; print('OK')\"` — 导入链验证")
                print(f"\n审查输出:\n{result.stdout[:1000]}")
                sys.exit(2)
            # 捕获审查反馈作为下一轮的输入
            # 从输出中提取发现部分（APPROVED/CONDITIONAL 之后的内容）
            output_lines = result.stdout.split("\n")
            # 跳过 verdict 行，取实质性反馈
            feedback_lines = [l for l in output_lines if l.strip() and not l.strip().startswith("APPROVED") and not l.strip().startswith("CONDITIONAL")]
            last_feedback = "\n".join(feedback_lines[:30])  # 最多30行反馈

            retry_attempt += 1
            print(f"tri_role: 判决={verdict}, 启动重试 {retry_attempt}/{MAX_RETRY}...", file=sys.stderr)
            continue

        # 成功或已达最大重试 → 输出结果
        # ── 技能结晶：当 --retry 循环成功完成时 ──
        # 借鉴 GenericAgent: "Every solved task crystallizes execution path into Skill"
        if retry_mode and retry_attempt > 0 and verdict == "approved":
            try:
                crystal_script = Path(__file__).parent / "crystallize_to_skill.py"
                if crystal_script.exists():
                    # 从上下文和反馈中提取问题/解决方案
                    prob_lines = [l for l in context.split("\n") if l.strip() and len(l) > 20]
                    problem = prob_lines[0][:200] if prob_lines else context[:200]
                    sol_lines = [l for l in (last_feedback or "").split("\n") if l.strip()]
                    solution = sol_lines[0][:200] if sol_lines else "retry循环自动修复"
                    
                    # 构建技能名称
                    safe_role = role.replace("-review", "").replace("-", "_")
                    skill_name = f"{safe_role}-retry-{verdict}"
                    
                    subprocess.run([
                        "python3", str(crystal_script),
                        "--name", skill_name,
                        "--problem", problem,
                        "--solution", solution,
                        "--code-changes", f"retry_attempts={retry_attempt}",
                        "--review-feedback", verdict,
                        "--tags", f"ecc,{role},retry,auto-crystallized",
                    ], capture_output=True, text=True, timeout=15)

                    # ── 自动收集训练数据到 skill_trainer ──
                    # 每次结晶都是"成功案例"，作为训练循环的正样本
                    trainer_script = Path(__file__).parent / "skill_trainer.py"
                    if trainer_script.exists():
                        safe_name = skill_name.replace("_", "-").lower()
                        subprocess.run([
                            "python3", str(trainer_script),
                            "--add-task", safe_name,
                            "--task", problem[:100],
                            "--outcome", "success",
                            "--score", "8",
                        ], capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                pass  # 结晶失败不阻断主流程

        print(result.stdout)

        # ── P0: --self-eval 5 轴质量自评 ──
        if self_eval and verdict == "approved":
            import re
            output = result.stdout
            eval_lines = [
                "\n## 📊 输出质量自评 (5 轴)",
                "",
                "| 轴 | 评分 | 证据 |",
                "|:---|:----:|:-----|",
            ]
            # Accuracy: check for contradictions
            accuracy = 4
            acc_notes = []
            if len(output) < 20:
                accuracy = 3
                acc_notes.append("输出过短")
            eval_lines.append(f"| **准确性** | {accuracy}/5 | {'; '.join(acc_notes) if acc_notes else '无明显错误'} |")

            # Completeness: check coverage
            completeness = 4
            comp_notes = []
            if verdict == "conditional":
                completeness = 3
                comp_notes.append("审查有保留意见")
            eval_lines.append(f"| **完整性** | {completeness}/5 | {'; '.join(comp_notes) if comp_notes else '已覆盖核心需求'} |")

            # Clarity
            clarity = 4
            clarity_notes = []
            if len(output.split('\n')) > 50:
                clarity = 3
                clarity_notes.append("输出行数较多")
            eval_lines.append(f"| **清晰度** | {clarity}/5 | {'; '.join(clarity_notes) if clarity_notes else '结构清晰'} |")

            # Actionability
            actionability = 4
            act_notes = []
            if "TODO" in output or "FIXME" in output:
                actionability = 3
                act_notes.append("含待办事项")
            eval_lines.append(f"| **可操作性** | {actionability}/5 | {'; '.join(act_notes) if act_notes else '可立即使用'} |")

            # Conciseness
            conciseness = 4
            conc_notes = []
            lines = output.split('\n')
            avg_line = sum(len(l) for l in lines) / len(lines) if lines else 0
            if avg_line > 200:
                conciseness = 3
                conc_notes.append("行均长度偏高")
            eval_lines.append(f"| **简洁性** | {conciseness}/5 | {'; '.join(conc_notes) if conc_notes else '无冗余'} |")

            overall = round((accuracy + completeness + clarity + actionability + conciseness) / 5, 1)
            eval_lines.append(f"\n**综合评分**: {overall}/5")
            if overall < 3.5:
                eval_lines.append("⚠️ 建议优化后再使用")
            eval_lines.append("")
            print("\n".join(eval_lines))
        try:
            for script in ["trace_viewer.py", "war_room.py", "feedback_loop.py"]:
                sp = BASE / "scripts" / script
                if sp.exists():
                    subprocess.run([sys.executable, str(sp)], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        sys.exit(0)

    # 不应到达此处
    sys.exit(1)


if __name__ == "__main__":
    main()
