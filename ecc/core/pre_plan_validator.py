#!/usr/bin/env python3
"""
pre_plan_validator.py — 前置规划验证门（P1-6）

基于 SPOQ 论文双门禁架构，在 ENGINE 执行前验证 WRITING-PLANS 质量。
规则引擎方式（无 V4 Pro 依赖），7 维度检查。

用法:
    python3 pre_plan_validator.py --arch <arch.md> --plans <plans.md>
    python3 pre_plan_validator.py --arch <arch.md> --plans <plans.md> --json   # JSON 输出给 gate()

返回:
    exit 0 = APPROVED (所有阻塞级通过)
    exit 1 = CONDITIONAL (仅警告级问题)
    exit 2 = FAIL (有阻塞级问题)
"""

import re
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple


# ── 维度定义 ──

DIMENSIONS = [
    {"id": 1, "name": "完整性", "weight": "blocker",
     "desc": "WRITING-PLANS 是否覆盖了 ARCH 设计中的所有组件/模块"},
    {"id": 2, "name": "可行性", "weight": "blocker",
     "desc": "每个子任务是否真的 ≤5 分钟"},
    {"id": 3, "name": "依赖顺序", "weight": "warning",
     "desc": "子任务的执行顺序是否正确"},
    {"id": 4, "name": "验证覆盖", "weight": "blocker",
     "desc": "每个子任务是否有明确的验证方式"},
    {"id": 5, "name": "范围边界", "weight": "warning",
     "desc": "是否有子任务超出了 ARCH 设计的范围"},
    {"id": 6, "name": "风险识别", "weight": "warning",
     "desc": "是否有高风险子任务需要特殊处理"},
    {"id": 7, "name": "路径一致性", "weight": "warning",
     "desc": "文件路径、模块名是否与 ARCH 设计一致"},
    {"id": 8, "name": "停滞检测", "weight": "warning",
     "desc": "检查当前规划是否与历史会话内容高度重复（Jaccard≥0.70且≥2次），防止反复循环"},
]

# ── 停滞检测工具函数 ──

def _ngram_set(text: str, n: int = 3) -> set:
    """字符级 n-gram 集合，用于快速相似度计算"""
    cleaned = re.sub(r'\s+', ' ', text.lower()).strip()
    return {cleaned[i:i+n] for i in range(len(cleaned) - n + 1)}


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard 相似度：交集大小 / 并集大小"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_stagnation(plans_text: str, session_log_path: str | None = None,
                     session_task_ids: list[str] | None = None) -> list[dict]:
    """维度8：停滞检测 — 检查规划是否与历史尝试高度重复"""
    issues = []

    # 从 PLANS 文本中提取任务描述
    plans_tasks = parse_plans_tasks(plans_text)
    task_descs = [t.get('desc', '') for t in plans_tasks if t.get('desc', '')]
    if not task_descs:
        return issues

    # 1) 计划内部重复检测：同一计划内有无几乎一模一样的子任务
    seen_ngrams = {}
    for t in task_descs:
        ng = _ngram_set(t)
        for prev_desc, prev_ng in seen_ngrams.items():
            sim = _jaccard_similarity(ng, prev_ng)
            if sim >= 0.80:
                issues.append({
                    "level": "warning",
                    "dim": "停滞检测",
                    "desc": f"子任务间高度相似 ({sim:.0%}): '{t[:40]}' 与 '{prev_desc[:40]}'",
                    "suggest": "合并重复子任务或重新分解"
                })
        seen_ngrams[t] = ng

    # 2) 历史会话重复检测
    if session_log_path:
        log_path = Path(session_log_path)
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            # 将日志按 PLANS 段切分
            plan_blocks = re.split(
                r'(?:##|###)\s*(?:WRITING.PLANS|任务分解|子任务)',
                log_text, flags=re.IGNORECASE
            )

            # 对每个历史 PLANS 段，计算与当前 PLANS 的相似度
            current_all = ' '.join(task_descs)
            current_ng = _ngram_set(current_all)

            high_sim_count = 0
            for block in plan_blocks[1:]:  # 跳过第一个（非段内容）
                block_stripped = block.strip()
                if len(block_stripped) < 50:
                    continue
                block_ng = _ngram_set(block_stripped)
                sim = _jaccard_similarity(current_ng, block_ng)
                if sim >= 0.70:
                    high_sim_count += 1

            if high_sim_count >= 2:
                issues.append({
                    "level": "warning",
                    "dim": "停滞检测",
                    "desc": f"当前规划与 {high_sim_count} 个历史 PLANS 段相似度≥70%，"
                            f"可能存在规划停滞",
                    "suggest": "考虑换一个方案方向，而非在当前方案上反复迭代"
                })

    return issues


def parse_arch_components(text: str) -> Dict[str, List[str]]:
    """从 ARCH 设计文档中提取组件/文件/模块信息"""
    components = []
    files = []
    modules = []

    # 提取文件路径: src/xxx.py, scripts/xxx.py 等
    for m in re.finditer(r'(?:src|scripts|tests|pipelines|config)[/\w.-]+\.(?:py|json|yaml|yml|toml)', text):
        fp = m.group(0)
        if fp not in files:
            files.append(fp)

    # 提取模块名 / 组件名: **xxx**、`xxx`、## xxx 等
    for m in re.finditer(r'\*\*([^*]+)\*\*', text):
        c = m.group(1).strip()
        if c and len(c) > 2 and c not in components:
            components.append(c)

    # 提取代码块中的文件名引用
    for m in re.finditer(r'`([^`]+\.(?:py|sh|json|yaml))`', text):
        fp = m.group(1)
        if fp not in files:
            files.append(fp)

    # 提取 # 章节标题中的关键名词
    for m in re.finditer(r'^#{1,3}\s+(.+)$', text, re.MULTILINE):
        title = m.group(1).strip()
        # 跳过模式名和重复项
        if title and len(title) > 3 and not title.startswith('###') and not title.startswith('---'):
            modules.append(title)

    return {
        "components": components,
        "files": files,
        "modules": modules,
    }


def parse_plans_tasks(text: str) -> List[Dict]:
    """从 WRITING-PLANS 中提取子任务列表"""
    tasks = []

    # 尝试解析表格格式
    in_table = False
    headers = []
    for line in text.split('\n'):
        line_stripped = line.strip()

        # 检测表格开始 (含 |的子任务表格)
        if line_stripped.startswith('|') and ('子任务' in line_stripped or '#' in line_stripped):
            # 解析表头
            cells = [c.strip() for c in line_stripped.strip('|').split('|')]
            headers = cells
            in_table = True
            continue

        if in_table and line_stripped.startswith('|') and re.match(r'^\|[\s:-]+\|', line_stripped):
            continue  # 跳过分隔行

        if in_table and line_stripped.startswith('|'):
            cells = [c.strip() for c in line_stripped.strip('|').split('|')]
            if len(cells) >= 3:
                task = {
                    "num": cells[0].strip(),
                    "desc": cells[1].strip() if len(cells) > 1 else "",
                    "file": cells[2].strip() if len(cells) > 2 else "",
                    "output": cells[3].strip() if len(cells) > 3 else "",
                    "verify": cells[4].strip() if len(cells) > 4 else "",
                }
                tasks.append(task)
        elif in_table and not line_stripped.startswith('|'):
            in_table = False

    # 如果没有表格，尝试从列表格式提取
    if not tasks:
        current_task = {}
        for line in text.split('\n'):
            m = re.match(r'^\s*(?:[-*]|\d+[.)])\s*(.+)$', line)
            if m:
                content = m.group(1).strip()
                if current_task and len(current_task.get('desc', '')) > 5:
                    tasks.append(current_task)
                    current_task = {}
                current_task = {"desc": content, "num": str(len(tasks) + 1),
                                "file": "", "output": "", "verify": ""}
            elif current_task and ':' in line:
                key, val = line.split(':', 1)
                k = key.strip().lower()
                if '文件' in k:
                    current_task['file'] = val.strip()
                elif '验证' in k or '测试' in k:
                    current_task['verify'] = val.strip()
                elif '输出' in k or '产出' in k:
                    current_task['output'] = val.strip()

        if current_task and current_task.get('desc', ''):
            tasks.append(current_task)

    return tasks


def check_completeness(arch: Dict, plans_tasks: List[Dict]) -> List[Dict]:
    """维度1：完整性检查"""
    issues = []
    plan_text = ' '.join(t.get('desc', '') + ' ' + t.get('file', '') for t in plans_tasks)

    # 检查 ARCH 中的文件是否在 PLANS 中被引用
    for f in arch.get('files', []):
        fname = Path(f).name  # 只用文件名（不含路径）
        if fname not in plan_text:
            issues.append({
                "level": "warning",
                "dim": "完整性",
                "desc": f"ARCH 引用的文件 '{f}' 在 WRITING-PLANS 中未出现",
                "suggest": f"添加对 {f} 的处理子任务"
            })

    # 检查 ARCH 中的组件是否被覆盖
    arch_components_lower = [c.lower() for c in arch.get('components', [])]
    for comp in arch.get('components', []):
        if comp.lower() not in plan_text.lower():
            issues.append({
                "level": "warning",
                "dim": "完整性",
                "desc": f"ARCH 组件 '{comp}' 在 WRITING-PLANS 中未被明确提及",
                "suggest": f"确认是否需要为 {comp} 添加子任务"
            })

    return issues


def check_feasibility(plans_tasks: List[Dict]) -> List[Dict]:
    """维度2：可行性检查"""
    issues = []
    for t in plans_tasks:
        desc = t.get('desc', '')
        # 标记包含大量工作的描述
        too_broad_keywords = ['全部', '所有', '重构', '迁移', '完整', '全量', '整体']
        for kw in too_broad_keywords:
            if kw in desc:
                issues.append({
                    "level": "warning",
                    "dim": "可行性",
                    "desc": f"子任务 #{t.get('num','?')} 含'{kw}'关键词，可能超过5分钟：'{desc[:60]}'",
                    "suggest": f"进一步拆解子任务 #{t.get('num','?')}"
                })
                break

        # 标记隐含多文件的任务
        if t.get('file', '') and '...' in t.get('file', ''):
            issues.append({
                "level": "warning",
                "dim": "可行性",
                "desc": f"子任务 #{t.get('num','?')} 文件路径含'...'，范围不明确",
                "suggest": f"明确每个子任务对应的具体文件路径"
            })

    return issues


def check_dependency_order(plans_tasks: List[Dict]) -> List[Dict]:
    """维度3：依赖顺序检查"""
    issues = []
    for i, t in enumerate(plans_tasks):
        file = t.get('file', '')
        desc = t.get('desc', '')

        # 检查"测试"是否在"实现"之前（错误顺序）
        if 'test' in desc.lower() or '测试' in desc:
            test_file = file
            # 向前查找是否有实现该文件的子任务
            found_predecessor = False
            for j in range(i):
                prev = plans_tasks[j]
                prev_desc = prev.get('desc', '')
                # 如果前序子任务"创建/实现/添加"了相同或类似内容
                if any(kw in prev_desc.lower() for kw in ['创建', '实现', 'implement', 'add', '编写']):
                    # 检查是否涉及同一文件
                    if prev.get('file', '') and test_file:
                        if Path(prev.get('file', '')).name in test_file or Path(test_file).name in prev.get('file', ''):
                            found_predecessor = True
                            break
            if not found_predecessor and i > 0:
                issues.append({
                    "level": "warning",
                    "dim": "依赖顺序",
                    "desc": f"子任务 #{t.get('num','?')} 是测试，但未找到前置实现任务",
                    "suggest": "确认测试可独立运行，或添加前置实现任务"
                })

    return issues


def check_verification(plans_tasks: List[Dict]) -> List[Dict]:
    """维度4：验证覆盖检查（阻塞级）"""
    issues = []

    # 每个任务必须有验证方式
    has_any_verify = any(t.get('verify', '') for t in plans_tasks)
    if not has_any_verify and plans_tasks:
        issues.append({
            "level": "blocker",
            "dim": "验证覆盖",
            "desc": f"所有 {len(plans_tasks)} 个子任务均无验证方式",
            "suggest": "为每个子任务添加验证列（py_compile / pytest / 人工检查）"
        })
        return issues

    for t in plans_tasks:
        verify = t.get('verify', '').strip()
        if not verify:
            issues.append({
                "level": "blocker",
                "dim": "验证覆盖",
                "desc": f"子任务 #{t.get('num','?')} 缺少验证方式：'{t.get('desc','')[:50]}'",
                "suggest": f"添加验证方式（如: python3 -m py_compile | pytest | 人工检查）"
            })

    return issues


def check_scope_boundary(arch: Dict, plans_tasks: List[Dict]) -> List[Dict]:
    """维度5：范围边界检查"""
    issues = []
    arch_files = set(arch.get('files', []))
    arch_text_lower = ' '.join(arch.get('components', []) + arch.get('modules', [])).lower()

    for t in plans_tasks:
        file = t.get('file', '')
        desc = t.get('desc', '')
        combined = (file + ' ' + desc).lower()

        # 检查是否涉及 ARCH 未提及的领域
        new_area_keywords = ['数据库', 'docker', 'k8s', 'kubernetes', 'redis', 'mq',
                             '消息队列', '缓存', '认证', '鉴权', '部署', '监控', '日志']
        for kw in new_area_keywords:
            if kw in combined:
                # 检查 ARCH 是否也提到过
                if kw not in arch_text_lower:
                    issues.append({
                        "level": "warning",
                        "dim": "范围边界",
                        "desc": f"子任务 #{t.get('num','?')} 涉及'{kw}'，ARCH 设计中未提及",
                        "suggest": f"确认'{kw}'是否应纳入本次任务范围"
                    })

    return issues


def check_risk(plans_tasks: List[Dict]) -> List[Dict]:
    """维度6：风险识别检查"""
    issues = []
    high_risk_keywords = {
        '并发': '并发/多线程操作易出现竞态条件',
        '多线程': '并发/多线程操作易出现竞态条件',
        'thread': '并发/多线程操作易出现竞态条件',
        '数据丢失': '数据丢失风险极大',
        '删除': '删除操作需确认备份',
        'rm -rf': '危险命令，需双确认',
        '新引入': '新依赖/框架引入学习成本和兼容性风险',
        '依赖': '外部依赖可能引入不稳定因素',
        '重构': '重构涉及面广，回退代价高',
        '迁移': '迁移过程中存在数据一致性问题',
    }

    for t in plans_tasks:
        combined = (t.get('desc', '') + ' ' + t.get('file', '')).lower()
        for kw, risk_desc in high_risk_keywords.items():
            if kw in combined:
                issues.append({
                    "level": "warning",
                    "dim": "风险识别",
                    "desc": f"子任务 #{t.get('num','?')} 含高风险关键词'{kw}': {risk_desc}",
                    "suggest": f"建议为该子任务增加前置验证步骤或回退点"
                })

    return issues


def check_path_consistency(arch: Dict, plans_tasks: List[Dict]) -> List[Dict]:
    """维度7：路径一致性检查"""
    issues = []
    arch_files = arch.get('files', [])

    for t in plans_tasks:
        plan_file = t.get('file', '')
        if not plan_file:
            continue
        plan_fname = Path(plan_file).name

        # 检查 ARCH 中是否有同名文件
        matching = [f for f in arch_files if Path(f).name == plan_fname]
        if matching:
            m = matching[0]
            if m != plan_file:
                # 路径不同但文件名相同，给出提示
                pass  # 可能是同一个文件的不同写法
        else:
            # PLANS 引用了 ARCH 未提及的文件
            issues.append({
                "level": "info",
                "dim": "路径一致性",
                "desc": f"子任务 #{t.get('num','?')} 文件 '{plan_file}' 在 ARCH 中未明确列出",
                "suggest": f"确认文件路径是否与 ARCH 设计一致"
            })

    return issues


def run_validation(arch_text: str, plans_text: str,
                   session_log_path: str | None = None) -> Dict:
    """运行完整的7维度验证"""
    arch = parse_arch_components(arch_text)
    plans_tasks = parse_plans_tasks(plans_text)

    # 7+1维度逐项检查
    checkers = [
        ("完整性", check_completeness(arch, plans_tasks)),
        ("可行性", check_feasibility(plans_tasks)),
        ("依赖顺序", check_dependency_order(plans_tasks)),
        ("验证覆盖", check_verification(plans_tasks)),
        ("范围边界", check_scope_boundary(arch, plans_tasks)),
        ("风险识别", check_risk(plans_tasks)),
        ("路径一致性", check_path_consistency(arch, plans_tasks)),
        ("停滞检测", check_stagnation(plans_text, session_log_path=session_log_path)),
    ]

    # 聚合
    blocker_issues = []
    warning_issues = []
    info_issues = []

    for dim_name, dim_issues in checkers:
        for issue in dim_issues:
            issue['dimension'] = dim_name
            if issue['level'] == 'blocker':
                blocker_issues.append(issue)
            elif issue['level'] == 'warning':
                warning_issues.append(issue)
            else:
                info_issues.append(issue)

    # 判决
    if blocker_issues:
        verdict = "FAIL"
        exit_code = 2
    elif warning_issues:
        verdict = "CONDITIONAL"
        exit_code = 1
    else:
        verdict = "APPROVED"
        exit_code = 0

    # 维度摘要
    dim_summary = {}
    for dim_name, dim_issues in checkers:
        n_blocker = sum(1 for i in dim_issues if i['level'] == 'blocker')
        n_warning = sum(1 for i in dim_issues if i['level'] == 'warning')
        if n_blocker > 0:
            status = "🔴"
        elif n_warning > 0:
            status = "🟡"
        else:
            status = "🟢"
        dim_summary[dim_name] = {
            "status": status,
            "blocker": n_blocker,
            "warning": n_warning,
        }

    result = {
        "verdict": verdict,
        "exit_code": exit_code,
        "blocker_count": len(blocker_issues),
        "warning_count": len(warning_issues),
        "info_count": len(info_issues),
        "dimensions": dim_summary,
        "issues": blocker_issues + warning_issues + info_issues,
        "arch_summary": {
            "components": len(arch.get('components', [])),
            "files": len(arch.get('files', [])),
            "modules": len(arch.get('modules', [])),
        },
        "plans_summary": {
            "tasks": len(plans_tasks),
        },
    }

    return result


def format_report(result: Dict) -> str:
    """格式化验证报告为可读文本"""
    lines = []
    lines.append("## 前置规划验证报告\n")
    lines.append(f"| 维度 | 状态 | 问题数 |")
    lines.append(f"|:----|:----|:------|")

    for dim_name, status_data in result.get('dimensions', {}).items():
        total = status_data['blocker'] + status_data['warning']
        lines.append(f"| {dim_name} | {status_data['status']} | {total} |")

    lines.append("")
    lines.append(f"**总计**: 🔴 {result['blocker_count']} 阻塞 + 🟡 {result['warning_count']} 警告 + ℹ️ {result['info_count']} 提示")
    lines.append("")

    if result['issues']:
        lines.append("### 待处理问题\n")
        for issue in result['issues']:
            level_tag = "🔴" if issue['level'] == 'blocker' else ("🟡" if issue['level'] == 'warning' else "ℹ️")
            level_str = "P0" if issue['level'] == 'blocker' else ("P1" if issue['level'] == 'warning' else "P2")
            lines.append(f"{level_tag} **{level_str}**: [{issue['dimension']}] {issue['desc']}")
            lines.append(f"   → {issue['suggest']}")
            lines.append("")
    else:
        lines.append("✅ 无待处理问题\n")

    lines.append(f"### 验证判决\n")
    verdict = result['verdict']
    if verdict == "APPROVED":
        lines.append(f"**{verdict}** ✅ — 所有维度通过，可以进入 ENGINE 阶段")
    elif verdict == "CONDITIONAL":
        lines.append(f"**{verdict}** 🟡 — 存在 {result['warning_count']} 个警告，建议修复后重新验证")
    else:
        lines.append(f"**{verdict}** 🔴 — 存在 {result['blocker_count']} 个阻塞级问题，必须返回 WRITING-PLANS 修复")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="前置规划验证门 — 8维度检查（含停滞检测）")
    parser.add_argument("--arch", required=True, help="ARCH 设计文档路径")
    parser.add_argument("--plans", required=True, help="WRITING-PLANS 分解文档路径")
    parser.add_argument("--session-log", type=str, default=None,
                        help="历史会话日志路径，用于停滞检测")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    arch_path = Path(args.arch)
    plans_path = Path(args.plans)

    if not arch_path.exists():
        print(f"错误: ARCH 文件不存在: {arch_path}", file=sys.stderr)
        sys.exit(2)
    if not plans_path.exists():
        print(f"错误: WRITING-PLANS 文件不存在: {plans_path}", file=sys.stderr)
        sys.exit(2)

    arch_text = arch_path.read_text(encoding="utf-8")
    plans_text = plans_path.read_text(encoding="utf-8")

    result = run_validation(arch_text, plans_text,
                            session_log_path=args.session_log)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
