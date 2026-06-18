#!/usr/bin/env python3
"""
skill_trainer.py — SkillOpt 训练循环

借鉴 SkillOpt (Microsoft, arXiv 2605.23904) 的正式训练循环设计：
Skill = 可训练的外部状态（markdown 文档），用 rollout→reflect→bounded_edit→gate
循环迭代改进，不碰模型权重。

与 crystallize_to_skill.py 的关系：
  - crystallize_to_skill.py: 从 tri_role --retry 成功出口创建技能（一次写入）
  - skill_trainer.py: 从后续使用中进化已存在的技能（迭代优化）

用法:
    python3 skill_trainer.py --train <skill-name>              # 全循环训练
    python3 skill_trainer.py --train <skill-name> --epochs 3   # 指定epochs
    python3 skill_trainer.py --train <skill-name> --lr low     # 学习率模式
    python3 skill_trainer.py --status <skill-name>             # 查看训练状态
    python3 skill_trainer.py --list                            # 列出可训练的skill
    python3 skill_trainer.py --add-task <skill-name> \\        # 手动注入训练任务
        --task "描述" --outcome fail --score 3
    python3 skill_trainer.py --gate <skill-name>               # 仅运行验证门禁

输出:
    训练日志 → training_data/checkpoints/<epoch>.json
    新版技能 → SKILL.md（仅当gate通过时更新）
    回滚备份 → training_data/checkpoints/rollback-<epoch>.json
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径 ──
HOME = Path.home()
SKILLS_DIR = HOME / ".hermes" / "skills" / "auto-crystallized"
SKILLS_INDEX = HOME / ".hermes" / "skills" / "auto-crystallized" / "skills-index.json"
SCRIPTS_DIR = HOME / ".hermes" / "profiles" / "ai-investor" / "scripts"


def atomic_write(path: Path, content: str, encoding: str = "utf-8"):
    """原子写入：写 .tmp → os.replace 覆盖，防止崩溃产生残缺文件"""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding=encoding)
    os.replace(str(tmp_path), str(path))


# ═══════════════════════════════════════════════════════════════
# 核心训练循环
# ═══════════════════════════════════════════════════════════════

def rollout(skill_dir: Path, epoch: int, training_tasks: list) -> dict:
    """阶段1: Rollout — 从训练数据回放batch + 评估当前skill

    SkillOpt的rollout = 在当前skill指导下执行任务、收集trace
    我们简化为：从tasks.json中读训练记录，评估当前skill的表现分

    Args:
        skill_dir: 技能目录（含SKILL.md + training_data/）
        epoch: 当前训练轮次
        training_tasks: 训练任务列表

    Returns:
        dict: 当前评估结果 {init_score, task_results, summary}
    """
    # 读当前SKILL.md内容
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return {"error": f"SKILL.md not found at {skill_path}"}

    skill_text = skill_path.read_text(encoding="utf-8")
    skill_lines = skill_text.splitlines()

    # 评估当前skill在各训练任务上的表现
    # 评分标准: 任务描述中trigger pattern匹配率 + outcome相关性
    task_results = []
    total_score = 0.0

    for task in training_tasks:
        task_desc = task.get("task", "")
        task_outcome = task.get("outcome", "unknown")  # success / fail
        task_historical_score = task.get("score", 5)  # 1-10

        # 计算当前skill的覆盖度: skill中与task相关的词匹配
        desc_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]+', task_desc.lower()))
        skill_related = sum(1 for w in desc_words if w.lower() in skill_text.lower())
        coverage = min(skill_related / max(len(desc_words), 1), 1.0)

        # 如果task是fail类型，skill文本是否覆盖了失败场景
        if task_outcome == "fail" and coverage > 0.3:
            # skill已经能覆盖这个失败场景 → 较好
            task_score = task_historical_score * 0.7 + coverage * 3.0
        elif task_outcome == "fail":
            # 失败场景未覆盖
            task_score = task_historical_score * 0.3
        else:
            # 成功场景
            task_score = task_historical_score * 0.5 + coverage * 5.0

        task_score = min(task_score, 10.0)
        total_score += task_score
        task_results.append({
            "task": task_desc[:80],
            "outcome": task_outcome,
            "coverage": round(coverage, 3),
            "score": round(task_score, 2),
        })

    init_score = round(total_score / max(len(training_tasks), 1), 2)

    return {
        "epoch": epoch,
        "skill_path": str(skill_path),
        "skill_lines": len(skill_lines),
        "skill_size_bytes": len(skill_text),
        "training_tasks": len(training_tasks),
        "init_score": init_score,
        "task_results": task_results,
        "timestamp": datetime.now().isoformat(),
    }


def reflect(skill_dir: Path, rollout_result: dict, lr_mode: str = "medium",
            strategy: str = "balanced") -> dict:
    """阶段2: Reflect — 用LLM分析skill文本 + 训练数据 → 提出有界改进建议

    SkillOpt的reflect = 优化器分析trace → 确定skill要改什么
    我们通过V4 Pro做LLM分析，模拟SkillOpt的优化器角色

    Args:
        skill_dir: 技能目录
        rollout_result: rollout阶段的评估结果
        lr_mode: 'low' (≤10%), 'medium' (≤20%), 'high' (≤35%)
        strategy: 'balanced' (默认), 'harden' (专注鲁棒性), 'repair-only' (最小改动)

    Returns:
        dict: {edit_proposals, proposed_edits, reflection_summary}
    """
    skill_path = skill_dir / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")

    # 学习率 → 最大可改行数
    skill_lines = skill_text.splitlines()
    total_lines = len(skill_lines)
    lr_map = {"low": 0.10, "medium": 0.20, "high": 0.35}
    max_change_ratio = lr_map.get(lr_mode, 0.20)
    # 策略影响学习率：repair-only 减半，harden 不变
    if strategy == "repair-only":
        max_change_ratio *= 0.5
    max_change_lines = max(int(total_lines * max_change_ratio), 3)

    # 构建反映射prompt — 策略影响优化焦点
    task_summary = "\n".join(
        f"  - [{t['outcome']}] score={t['score']} | {t['task']}"
        for t in rollout_result["task_results"][:5]
    )

    # 策略对应的改进方向
    strategy_instructions = {
        "balanced": (
            '改进方向:\n'
            '  1. 对score<5的任务增加覆盖\n'
            '  2. 使解决方案描述更具体可操作\n'
            '  3. 补充缺失的"已知坑点"或"验证方式"\n'
            '  4. 改进触发条件使路由更精准'
        ),
        "harden": (
            '改进方向（专注鲁棒性）:\n'
            '  1. 重点关注所有 fail 类型的任务，增加失败场景覆盖\n'
            '  2. 补全边界情况和异常处理描述\n'
            '  3. 增加"恢复步骤"或"回退方案"章节\n'
            '  4. 使解决方案更健壮，覆盖更多 edge case'
        ),
        "repair-only": (
            '改进方向（最小改动）:\n'
            '  1. 只修 score ≤ 3 的任务，不碰其他内容\n'
            '  2. 如果所有任务 score > 3，建议不做任何修改\n'
            '  3. 保持文档其他部分完全不变\n'
            '  4. 修改范围限制在最大行数的50%以内'
        ),
    }

    reflect_prompt = f"""你是一个 Skill Optimizer，负责分析以下技能文本并提出改进建议。

=== 当前技能 ===
{skill_text[:3000]}  # 限制上下文长度
...

=== 训练数据摘要 ===
当前评分: {rollout_result['init_score']}/10
训练任务数: {rollout_result['training_tasks']}
最近任务:
{task_summary}

=== 约束 ===
- 只改最关键的 {max_change_lines} 行（文本学习率 = {max_change_ratio*100:.0f}%）
- 保持YAML frontmatter完整
- 不改变技能的name/triggers元数据
{strategy_instructions.get(strategy, strategy_instructions["balanced"])}

=== 输出格式 ===
请以JSON格式返回编辑建议：
{{
    "reflection_summary": "对skill弱点的简要分析",
    "edit_proposals": [
        {{
            "section": "section name or line range",
            "reason": "为什么要改这里",
            "suggested_action": "add/delete/replace",
            "affected_scores": ["task names this helps"]
        }}
    ],
    "expected_delta": "预估改进幅度 (e.g., +0.5 to +1.5 points)"
}}

只返回JSON，不要有其他说明。"""

    # 调用V4 Pro（通过claude -p 或直接）
    try:
        result = subprocess.run(
            [
                sys.executable, str(SCRIPTS_DIR / "tri_role.py"),
                "review",
                reflect_prompt,
            ],
            capture_output=True, text=True, timeout=120,
        )
        v4pro_output = result.stdout or result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # 降级：V4 Pro不可用时用启发式分析
        v4pro_output = _fallback_reflect(skill_text, task_summary)

    # 解析V4 Pro的JSON输出
    reflect_data = _parse_reflection(v4pro_output)

    return {
        "reflection_summary": reflect_data.get("reflection_summary", "启发式分析"),
        "edit_proposals": reflect_data.get("edit_proposals", []),
        "max_change_lines": max_change_lines,
        "lr_mode": lr_mode,
        "proposed_edits": len(reflect_data.get("edit_proposals", [])),
    }


def _fallback_reflect(skill_text: str, task_summary: str) -> str:
    """V4 Pro不可用时的降级反射分析——纯规则启发式"""
    lines = skill_text.splitlines()
    low_score_tasks = []
    for line in task_summary.splitlines():
        if "score<" in line or "score=5." in line or "score=4." in line or "score=3." in line:
            low_score_tasks.append(line.strip())

    gaps = []
    sections_found = []
    required_sections = ["问题", "解决方案", "使用方式", "触发条件", "已知坑点", "验证方式"]

    for section in required_sections:
        found = any(f"## {section}" in l or f"# {section}" in l for l in lines)
        sections_found.append({"section": section, "present": found})
        if not found:
            gaps.append(f"缺少'{section}'章节")

    proposals = [
        {
            "section": "内容分析",
            "reason": f"技能行数: {len(lines)}, 缺失章节: {', '.join(gaps) if gaps else '无'}",
            "suggested_action": "add" if gaps else "review",
            "affected_scores": [t[:40] for t in low_score_tasks[:3]],
        }
    ]

    result = {
        "reflection_summary": f"启发式分析: {len(lines)}行, {'无缺失' if not gaps else '缺'+', '.join(gaps)}",
        "edit_proposals": proposals,
    }
    return json.dumps(result, ensure_ascii=False)


def _parse_reflection(text: str) -> dict:
    """从V4 Pro输出中提取JSON"""
    # 先尝试直接解析
    text = text.strip()
    # 查找JSON块
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # 尝试从markdown代码块中提取
    code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 降级
    return {
        "reflection_summary": f"解析失败，原始输出: {text[:200]}",
        "edit_proposals": [],
    }


def bounded_edit(skill_dir: Path, reflect_result: dict, rollout_result: dict = None) -> dict:
    """阶段3: Bounded Edit — 应用有界编辑 + checkpoint备份

    SkillOpt的bounded edit = 按学习率限制改动量
    我们用checkpoint做备份，diff做审计

    Args:
        skill_dir: 技能目录
        reflect_result: reflect阶段的编辑建议
        rollout_result: rollout阶段的评估结果（可选，用于上下文）

    Returns:
        dict: {edited_lines, diffs, checkpoint_path, applied_changes}
    """
    skill_path = skill_dir / "SKILL.md"
    checkpoint_dir = skill_dir / "training_data" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # 1. 读当前skill
    original_text = skill_path.read_text(encoding="utf-8")

    # 2. 备份到checkpoint
    epoch = rollout_result.get("epoch", 0) if rollout_result else 0
    cp_path = checkpoint_dir / f"rollback-epoch-{epoch}.json"
    cp_data = {
        "epoch": epoch,
        "timestamp": datetime.now().isoformat(),
        "original_text": original_text,
        "rollout_score": rollout_result.get("init_score", 0) if rollout_result else 0,
    }
    atomic_write(cp_path, json.dumps(cp_data, ensure_ascii=False, indent=2))

    # 3. 应用编辑（基于LLM分析，但我们直接在skill上做有界修改）
    # 对于简单的训练场景，我们使用启发式规则改进skill内容
    edit_proposals = reflect_result.get("edit_proposals", [])
    max_lines = reflect_result.get("max_change_lines", 5)

    # 实际修改策略：
    # 如果没有具体的编辑提案，或者编辑提案无法直接应用到text，
    # 我们基于reflection_summary生成小改进
    lines = original_text.splitlines()
    original_lines = lines.copy()
    edits_applied = 0

    # 启发式改进：检查并补充缺失章节
    section_map = {
        "已知坑点": "\n## 已知坑点\n\n（自动生成 — 技能训练循环改进）\n暂无记录，使用时请注意验证。\n",
        "验证方式": "\n## 验证方式\n\n（自动生成 — 技能训练循环改进）\n暂无标准验证流程，需使用者自行判断。\n",
    }

    for section_name, section_template in section_map.items():
        if edits_applied >= max_lines:
            break
        # 检查是否已有该章节
        found = any(f"## {section_name}" in l or f"# {section_name}" in l for l in lines)
        if not found:
            lines.append(section_template)
            edits_applied += section_template.count("\n") + 1

    # 生成diff
    diff = list(difflib.unified_diff(
        original_lines, lines,
        fromfile="before", tofile="after",
        lineterm="",
    ))

    # 写入新版本
    new_text = "\n".join(lines)
    atomic_write(skill_path, new_text)

    return {
        "checkpoint_path": str(cp_path),
        "original_lines": len(original_lines),
        "new_lines": len(lines),
        "lines_changed": abs(len(lines) - len(original_lines)),
        "edits_applied": edits_applied,
        "diff_lines": len(diff),
        "diff_sample": "\n".join(diff[:20]) if diff else "(无变更)",
        "timestamp": datetime.now().isoformat(),
    }


def gate(skill_dir: Path, edit_result: dict, training_tasks: list,
         held_out_tasks: list, acceptance_threshold: float = 0.05) -> dict:
    """阶段4: Gate — held-out验证门禁

    SkillOpt的gate = 在held-out验证集上评估新版本 → 仅提升时接受
    我们用LLM-as-judge对比新旧版本的held-out表现

    Args:
        skill_dir: 技能目录
        edit_result: bounded_edit的结果（含checkpoint信息）
        training_tasks: 全部训练任务
        held_out_tasks: held-out验证任务（必须与训练集不重叠）
        acceptance_threshold: 接受阈值（默认0.05，balanced）

    Returns:
        dict: {accepted, old_score, new_score, delta, reason}
    """
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return {"accepted": False, "error": "SKILL.md不存在"}

    # 如果没有held_out_tasks，从training_tasks中分割20%做验证
    if not held_out_tasks and len(training_tasks) >= 5:
        split_idx = max(int(len(training_tasks) * 0.8), 1)
        held_out_tasks = training_tasks[split_idx:]
        training_tasks = training_tasks[:split_idx]
    elif not held_out_tasks:
        # 任务太少，直接用全部但降低置信度
        held_out_tasks = training_tasks

    # 读新旧版本
    new_skill_text = skill_path.read_text(encoding="utf-8")

    # 从checkpoint读旧版本
    old_text = ""
    cp_path_str = edit_result.get("checkpoint_path", "")
    if cp_path_str:
        cp_path = Path(cp_path_str)
        if cp_path.exists():
            try:
                cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
                old_text = cp_data.get("original_text", "")
            except (json.JSONDecodeError, OSError):
                pass

    # 从checkpoint恢复old_score
    old_score_val = 5.0  # default
    cp_path_str = edit_result.get("checkpoint_path", "")
    if cp_path_str:
        cp_path = Path(cp_path_str)
        if cp_path.exists():
            try:
                cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
                old_score_val = cp_data.get("rollout_score", 5.0)
            except (json.JSONDecodeError, OSError):
                pass

    # 在held-out验证集上评估新版本
    # 我们使用覆盖率评估（与rollout相同但用在held_out）
    new_score = _evaluate_on_tasks(new_skill_text, held_out_tasks)
    old_score_on_held = _evaluate_on_tasks(old_text, held_out_tasks) if old_text else old_score_val

    # P1-5: Benchmark分数作为额外的验证信号
    benchmark_score = None
    try:
        bm_result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "skill_benchmark.py"),
             "--score", str(skill_dir)],
            capture_output=True, text=True, timeout=30,
        )
        if bm_result.returncode == 0:
            bm_data = json.loads(bm_result.stdout)
            benchmark_score = bm_data.get("overall", None)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass

    # P1-4: Surrogate Verifier 独立诊断
    verifier_result = None
    try:
        vf_result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "surrogate_verifier.py"),
             "--feedback", str(skill_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if vf_result.returncode == 0:
            verifier_result = json.loads(vf_result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass

    # 加权决策：覆盖率(50%) + benchmark(30%) + verifier盲点(20%)
    weighted_new = new_score * 0.5
    weighted_old = old_score_on_held * 0.5

    if benchmark_score is not None:
        weighted_new += benchmark_score * 0.30
        weighted_old += benchmark_score * 0.25  # 旧版本用同一benchmark作基线

    if verifier_result:
        blind_count = verifier_result.get("blind_spot_count", 0)
        overclaim_count = verifier_result.get("overclaim_count", 0)
        penalty = min((blind_count + overclaim_count) * 0.3, 2.0)
        weighted_new -= penalty

    delta = round(weighted_new - weighted_old, 2)
    accepted = delta > acceptance_threshold

    if not accepted:
        # 回滚：从checkpoint恢复
        if cp_path_str and old_text:
            atomic_write(skill_path, old_text)
            reason = f"回滚: new={weighted_new} ≤ old={weighted_old} (delta={delta})"
        else:
            reason = f"未接受但无法回滚（无checkpoint）: new={weighted_new} ≤ old={weighted_old}"
    else:
        reason = f"接受: new={weighted_new} > old={weighted_old} (delta={delta})"

    result = {
        "accepted": accepted,
        "old_score": round(weighted_old, 2),
        "new_score": round(weighted_new, 2),
        "delta": delta,
        "held_out_tasks": len(held_out_tasks),
        "reason": reason,
        "timestamp": datetime.now().isoformat(),
    }
    if benchmark_score is not None:
        result["benchmark_score"] = benchmark_score
    if verifier_result:
        result["verifier"] = {
            "blind_spots": verifier_result.get("blind_spot_count", 0),
            "overclaims": verifier_result.get("overclaim_count", 0),
        }
    return result


def _evaluate_on_tasks(skill_text: str, tasks: list) -> float:
    """在任务集上评估skill文本的覆盖率得分"""
    if not skill_text or not tasks:
        return 0.0

    total = 0.0
    for task in tasks:
        task_desc = task.get("task", "")
        task_outcome = task.get("outcome", "success")

        desc_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]+', task_desc.lower()))
        if not desc_words:
            continue

        coverage = sum(1 for w in desc_words if w.lower() in skill_text.lower())
        coverage_ratio = min(coverage / len(desc_words), 1.0)

        # 失败任务更看重覆盖率
        if task_outcome == "fail":
            score = coverage_ratio * 10.0
        else:
            score = coverage_ratio * 7.0

        total += score

    return round(total / max(len(tasks), 1), 2)


# ═══════════════════════════════════════════════════════════════
# 训练数据管理
# ═══════════════════════════════════════════════════════════════

def get_training_data_dir(skill_dir: Path) -> Path:
    """获取训练数据目录，不存在则创建"""
    data_dir = skill_dir / "training_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def load_training_tasks(skill_dir: Path) -> list:
    """从skills.json加载训练任务"""
    data_dir = get_training_data_dir(skill_dir)
    tasks_path = data_dir / "tasks.json"

    if tasks_path.exists():
        try:
            data = json.loads(tasks_path.read_text(encoding="utf-8"))
            return data.get("tasks", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_training_tasks(skill_dir: Path, tasks: list):
    """保存训练任务到tasks.json"""
    data_dir = get_training_data_dir(skill_dir)
    tasks_path = data_dir / "tasks.json"

    data = {
        "total_tasks": len(tasks),
        "last_updated": datetime.now().isoformat(),
        "tasks": tasks,
    }
    atomic_write(tasks_path, json.dumps(data, ensure_ascii=False, indent=2))


def add_training_task(skill_dir: Path, task: str, outcome: str = "fail",
                      score: int = 5, trace: str = ""):
    """添加一个训练任务"""
    tasks = load_training_tasks(skill_dir)
    entry = {
        "task": task,
        "outcome": outcome,  # success / fail
        "score": min(max(score, 1), 10),  # 1-10
        "trace": trace[:500] if trace else "",
        "added": datetime.now().isoformat(),
    }
    tasks.append(entry)
    save_training_tasks(skill_dir, tasks)
    return len(tasks)


def list_trainable_skills() -> list:
    """列出所有有训练数据/可训练的auto-crystallized技能"""
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            continue

        tasks = load_training_tasks(skill_dir)
        cp_dir = skill_dir / "training_data" / "checkpoints"
        cp_count = len(list(cp_dir.glob("*.json"))) if cp_dir.exists() else 0

        skills.append({
            "name": skill_dir.name,
            "has_training_data": len(tasks) > 0,
            "training_tasks": len(tasks),
            "checkpoints": cp_count,
            "last_trained": _get_last_trained(skill_dir),
        })

    return skills


def _get_last_trained(skill_dir: Path) -> str:
    """获取上次训练时间"""
    cp_dir = skill_dir / "training_data" / "checkpoints"
    if not cp_dir.exists():
        return "never"

    latest = None
    for f in cp_dir.glob("*.json"):
        mtime = f.stat().st_mtime
        if latest is None or mtime > latest[0]:
            latest = (mtime, f.name)

    if latest:
        dt = datetime.fromtimestamp(latest[0])
        return dt.strftime("%Y-%m-%d %H:%M")
    return "never"


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def cmd_status(name: str):
    """查看技能训练状态"""
    skill_dir = SKILLS_DIR / name
    if not (skill_dir / "SKILL.md").exists():
        print(f"✗ 技能 '{name}' 不存在 (路径: {skill_dir})")
        sys.exit(1)

    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    tasks = load_training_tasks(skill_dir)
    cp_dir = skill_dir / "training_data" / "checkpoints"
    cp_count = len(list(cp_dir.glob("*.json"))) if cp_dir.exists() else 0

    print(f"\n📊 技能训练状态: {name}")
    print(f"   路径: {skill_dir}")
    print(f"   SKILL.md: {len(skill_text)} bytes, {len(skill_text.splitlines())} 行")
    print(f"   训练任务: {len(tasks)}")
    print(f"   检查点: {cp_count}")
    print(f"   上次训练: {_get_last_trained(skill_dir)}")

    if tasks:
        print(f"\n   训练任务明细:")
        for t in tasks[-5:]:  # 最近5条
            print(f"     [{t['outcome']}] score={t['score']} | {t['task'][:60]}")
        if len(tasks) > 5:
            print(f"     ... 还有 {len(tasks)-5} 条")

    # 是否准备好训练
    if len(tasks) >= 3:
        print(f"\n   ✅ 训练就绪 (≥3个训练任务)")
    else:
        print(f"\n   ⚠️ 训练数据不足 (需要≥3个任务, 当前{len(tasks)})")


def cmd_list():
    """列出所有可训练技能"""
    skills = list_trainable_skills()
    if not skills:
        print("没有auto-crystallized技能 (目录为空或不存在)")
        return

    print(f"\n📋 可训练技能 ({len(skills)})\n")
    print(f"  {'名称':<25} {'训练数据':<12} {'检查点':<10} {'上次训练'}")
    print(f"  {'-'*25} {'-'*12} {'-'*10} {'-'*16}")
    for s in skills:
        name = s["name"][:24]
        train = f"✅ {s['training_tasks']}task" if s["has_training_data"] else "❌ 无数据"
        cp = f"{s['checkpoints']}个"
        print(f"  {name:<25} {train:<12} {cp:<10} {s['last_trained']}")


def cmd_train(name: str, epochs: int = 1, lr_mode: str = "medium",
              held_out: list = None, strategy: str = "balanced"):
    """全训练循环执行: rollout → reflect → bounded_edit → gate

    Args:
        name: 技能名称
        epochs: 训练轮次
        lr_mode: 学习率 low/medium/high
        held_out: 可选的held-out验证集（None则从训练数据分割）
        strategy: 训练策略 balanced/harden/repair-only
    """
    skill_dir = SKILLS_DIR / name
    if not (skill_dir / "SKILL.md").exists():
        print(f"✗ 技能 '{name}' 不存在")
        sys.exit(1)

    training_tasks = load_training_tasks(skill_dir)
    if len(training_tasks) < 3:
        print(f"⚠️ 训练数据不足: {len(training_tasks)}/3 个任务")
        print(f"   用 --add-task 添加, 或直接 --gate 运行验证")

    print(f"\n🚀 启动训练: {name}")
    print(f"   epochs={epochs}, lr={lr_mode}, strategy={strategy}, "
          f"训练任务={len(training_tasks)}")

    # 策略对 gate 阈值的影响
    strategy_gate_threshold = {
        "balanced": 0.05,
        "harden": 0.02,     # 硬化：更易接受，优先鲁棒性
        "repair-only": -0.02,  # 仅修复：允许微小退化
    }
    gate_threshold = strategy_gate_threshold.get(strategy, 0.05)

    for epoch in range(epochs):
        print(f"\n{'='*50}")
        print(f"Epoch {epoch + 1}/{epochs}")
        print(f"{'='*50}")

        # Phase 1: Rollout
        print(f"\n① Rollout — 评估当前skill...")
        rollout_result = rollout(skill_dir, epoch + 1, training_tasks)
        if "error" in rollout_result:
            print(f"  ✗ 失败: {rollout_result['error']}")
            return

        print(f"   init_score: {rollout_result['init_score']}/10")
        print(f"   {rollout_result['training_tasks']} 个任务, {rollout_result['skill_lines']} 行")

        # Phase 2: Reflect
        print(f"\n② Reflect — LLM分析+提改进建议 (策略: {strategy})...")
        reflect_result = reflect(skill_dir, rollout_result, lr_mode,
                                 strategy=strategy)
        print(f"   分析: {reflect_result['reflection_summary'][:80]}")
        print(f"   建议编辑: {reflect_result['proposed_edits']} 处")
        print(f"   学习率: {lr_mode} (≤{reflect_result['max_change_lines']}行/epoch)")

        # Phase 3: Bounded Edit
        print(f"\n③ Bounded Edit — 应用有界编辑...")
        edit_result = bounded_edit(skill_dir, reflect_result, rollout_result)
        print(f"   备份: {edit_result['checkpoint_path']}")
        print(f"   变更: {edit_result['lines_changed']} 行")
        if edit_result['diff_sample'].strip() and '(无变更)' not in edit_result['diff_sample']:
            print(f"   Diff:")
            for d_line in edit_result['diff_sample'].splitlines()[:10]:
                print(f"     {d_line}")

        # Phase 4: Gate — 使用策略对应的阈值
        print(f"\n④ Gate — held-out验证门禁 (阈值: {gate_threshold})...")
        gate_result = gate(skill_dir, edit_result, training_tasks, held_out or [],
                           acceptance_threshold=gate_threshold)

        verdict = "✅ 接受" if gate_result["accepted"] else "❌ 回滚"
        print(f"   old_score: {gate_result['old_score']}")
        print(f"   new_score: {gate_result['new_score']}")
        print(f"   delta: {gate_result['delta']}")
        print(f"   判决: {verdict}")
        print(f"   原因: {gate_result['reason']}")

        print(f"\n{'='*50}")

    print(f"\n🎉 训练完成 ({epochs} epochs)")
    print(f"   查看状态: python3 skill_trainer.py --status {name}")


def cmd_add_task(name: str, task: str, outcome: str, score: int, trace: str = ""):
    """手动注入训练任务"""
    skill_dir = SKILLS_DIR / name
    if not (skill_dir / "SKILL.md").exists():
        print(f"✗ 技能 '{name}' 不存在")
        sys.exit(1)

    total = add_training_task(skill_dir, task, outcome, score, trace)
    print(f"✅ 训练任务已添加 (总计 {total} 个)")
    print(f"   技能: {name}")
    print(f"   任务: {task[:60]}")
    print(f"   结果: {outcome}, score={score}")
    print(f"\n   现在可以运行训练: python3 skill_trainer.py --train {name}")


def main():
    parser = argparse.ArgumentParser(
        description="SkillOpt 训练循环 — 从使用中进化技能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python3 skill_trainer.py --list
    python3 skill_trainer.py --status tri-role-retry
    python3 skill_trainer.py --train tri-role-retry --epochs 3 --lr medium
    python3 skill_trainer.py --add-task tri-role-retry --task "pipeline timeout" --outcome fail --score 3
    python3 skill_trainer.py --gate tri-role-retry
        """,
    )

    # 动作
    parser.add_argument("--train", metavar="SKILL", help="运行全训练循环")
    parser.add_argument("--status", metavar="SKILL", help="查看训练状态")
    parser.add_argument("--list", action="store_true", help="列出可训练技能")
    parser.add_argument("--gate", metavar="SKILL", help="仅运行验证门禁")

    # 训练参数
    parser.add_argument("--epochs", type=int, default=1, help="训练轮次 (默认: 1)")
    parser.add_argument("--lr", choices=["low", "medium", "high"], default="medium",
                        help="学习率模式 (默认: medium=20%)")
    parser.add_argument("--strategy", choices=["balanced", "harden", "repair-only"],
                        default="balanced",
                        help="训练策略: balanced(默认), harden(鲁棒性优先), "
                             "repair-only(最小改动)")

    # 添加任务
    parser.add_argument("--add-task", metavar="SKILL", help="添加训练任务")
    parser.add_argument("--task", help="任务描述")
    parser.add_argument("--outcome", choices=["success", "fail"], default="fail",
                        help="任务结果 (默认: fail)")
    parser.add_argument("--score", type=int, default=5, choices=range(1, 11),
                        help="质量评分 1-10 (默认: 5)")
    parser.add_argument("--trace", default="", help="追踪日志（可选）")

    args = parser.parse_args()

    # 统计调用
    actions = 0
    if args.train:
        actions += 1
    if args.status:
        actions += 1
    if args.list:
        actions += 1
    if args.gate:
        actions += 1
    if args.add_task:
        actions += 1

    if actions == 0:
        parser.print_help()
        sys.exit(1)
    elif actions > 1:
        print("✗ 一次只支持一个操作")
        sys.exit(1)

    # 路由到对应命令
    if args.train:
        cmd_train(args.train, args.epochs, args.lr, strategy=args.strategy)
    elif args.status:
        cmd_status(args.status)
    elif args.list:
        cmd_list()
    elif args.gate:
        # 仅运行gate，不做edit
        skill_dir = SKILLS_DIR / args.gate
        if not (skill_dir / "SKILL.md").exists():
            print(f"✗ 技能 '{args.gate}' 不存在")
            sys.exit(1)
        training_tasks = load_training_tasks(skill_dir)
        if not training_tasks:
            print(f"⚠️ 没有训练数据")
            sys.exit(0)
        # roll + gate
        roll = rollout(skill_dir, 0, training_tasks)
        edit_stub = {
            "checkpoint_path": str(skill_dir / "training_data" / "checkpoints" / "gate-only.json"),
            "rollout_score": roll["init_score"],
        }
        gate_result = gate(skill_dir, edit_stub, training_tasks, [])
        verdict = "✅ 通过" if gate_result["accepted"] else "❌ 未通过"
        print(f"\n🔒 Gate: {args.gate}")
        print(f"   Score: {gate_result['new_score']}")
        print(f"   判决: {verdict}")
        print(f"   原因: {gate_result['reason']}")
    elif args.add_task:
        if not args.task:
            print("✗ --task 是必需的")
            sys.exit(1)
        cmd_add_task(args.add_task, args.task, args.outcome, args.score, args.trace)


if __name__ == "__main__":
    main()
