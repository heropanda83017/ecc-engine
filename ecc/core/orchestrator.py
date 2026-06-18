#!/usr/bin/env python3
"""
orchestrator.py — ECC 工作流编排器 (Orchestrator Pattern)

源自 structural-thinking 的"Three Buckets and an Orchestrator"模式。
根据任务大小分类，路由到不同的 ECC 工作流：

  small-change:   单文件 ≤30行 → 快速审查 + FINAL-REVIEW
  medium-feature: 单文件 ≥30行 → 标准 8 角色流水线
  large-project:  跨模块多文件 → 完整 8 角色 + ADR + 并行

用法:
    python3 orchestrator.py "修复 typo"              # → small
    python3 orchestrator.py "新增因子计算函数"        # → medium
    python3 orchestrator.py "重构整个回测引擎"        # → large
"""

import sys, json, re
from pathlib import Path


def classify_task(description: str) -> dict:
    """P0: 根据任务描述自动分类为 small/medium/large"""
    desc_lower = description.lower()
    
    # 信号词检测
    small_signals = ["typo", "bug", "fix", "修复", "修改", "删除", "rename", "重命名",
                     "small", "minor", "tiny", "quick", "快速", "简单", "一行"]
    medium_signals = ["feature", "功能", "新增", "add", "implement", "实现",
                      "refactor", "重构", "优化", "improve", "enhance"]
    large_signals = ["project", "项目", "system", "系统", "architecture", "架构",
                     "large", "大型", "multi", "多模块", "跨", "platform", "平台",
                     "rewrite", "重写", "migration", "迁移", "overhaul"]
    
    # 文件/代码量估算
    file_indicators = re.findall(r'(\d+)\s*(?:个|个文件|files?|modules?)', desc_lower)
    estimated_files = sum(int(x) for x in file_indicators) if file_indicators else 0
    
    # 计分
    score = 0
    if any(s in desc_lower for s in small_signals):
        score -= 1
    if any(s in desc_lower for s in medium_signals):
        score += 1
    if any(s in desc_lower for s in large_signals):
        score += 2
    if estimated_files >= 5:
        score += 2
    elif estimated_files >= 2:
        score += 1
    
    if score <= 0:
        bucket = "small-change"
    elif score <= 2:
        bucket = "medium-feature"
    else:
        bucket = "large-project"
    
    return {
        "bucket": bucket,
        "score": score,
        "estimated_files": estimated_files or None,
        "description": description,
    }


def get_workflow(bucket: str) -> str:
    """返回对应的工作流步骤"""
    workflows = {
        "small-change": """
【Small Change 工作流】
1. ENGINE — 直接实现
2. SPEC-REVIEWER — 规范审查（并行）
3. CODE-QUALITY-REVIEWER — 质量审查（并行）
4. FINAL-REVIEW — 最终审查
5. WIKI-ARCHIVE — 归档
""",
        "medium-feature": """
【Medium Feature 工作流】
1. WRITING-PLANS — 任务拆解 ≤5min
2. PRE-PLAN-VALIDATION — 8 维验证门
3. ENGINE — 逐项实现
4. SPEC-REVIEWER + CQR (并行)
5. RECEIVING-CODE-REVIEW — 反馈处理
6. FINAL-REVIEW — 最终审查
7. WIKI-ARCHIVE — 归档
""",
        "large-project": """
【Large Project 工作流】
1. ARCH — 架构设计（含 ADR）
2. REVIEW — 设计审查
3. WRITING-PLANS — 任务拆解
4. PRE-PLAN-VALIDATION — 8 维验证门
5. ENGINE — 逐模块实现（可并行）
6. SPEC-REVIEWER + CQR (并行)
7. RECEIVING-CODE-REVIEW — 反馈处理
8. FINAL-REVIEW — 最终审查
9. WIKI-ARCHIVE — 归档
""",
    }
    return workflows.get(bucket, workflows["medium-feature"])


def main():
    if len(sys.argv) < 2:
        desc = input("描述任务: ")
    else:
        desc = " ".join(sys.argv[1:])
    
    result = classify_task(desc)
    print(f"\n🔍 ECC Orchestrator")
    print(f"   任务: {result['description']}")
    print(f"   分类: {result['bucket']} (score={result['score']})")
    if result['estimated_files']:
        print(f"   估算文件: ~{result['estimated_files']} 个")
    print(get_workflow(result['bucket']))


if __name__ == "__main__":
    main()
