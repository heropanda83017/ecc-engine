# ECC Engine — Engineering Control Chain

> v2.9.1 | MIT License | 独立封装的 Agent 工程编码质量保障体系

## 定位

ECC (Engineering Control Chain) 是一套完整的 Agent 工程编码流水线——从架构设计到代码实现到质量审查到 Wiki 归档，8 个角色各司其职。不是"写完代码再检查"，而是**每一步都有门禁**。

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 配置模型（可选，默认使用 claude CLI）
export ECC_MODEL_CMD="claude --bare --model opus"
export ECC_FAST_MODEL_CMD="claude --bare --model haiku"

# 3. 配置数据目录（可选）
export ECC_HOME=$(pwd)

# 4. 运行审查
ecc-review final-review '审查以下代码: def add(a,b): return a+b'

# 5. 系统诊断
ecc-audit
ecc-evolve
```

## 命令行工具

| 命令 | 对应脚本 | 说明 |
|:-----|:---------|:------|
| `ecc-review` | `tri_role.py` | 8 角色审查调用器 |
| `ecc-shield` | `agentshield_check.py` | 22 条安全/质量检测 |
| `ecc-parallel` | `parallel_review.py` | 并行审查 |
| `ecc-audit` | `agent_audit.py` | 12 层 Agent 系统诊断 |
| `ecc-evolve` | `evolution_analyzer.py` | 审查趋势分析 |
| `ecc-debt` | `debt_scanner.py` | 技术债务扫描 |
| `ecc-bench` | `ecc_benchmark.py` | ECC vs baseline 基准测试 |

## 8 角色流水线

```
ARCH → REVIEW → WRITING-PLANS → PRE-PLAN-VALIDATION → ENGINE 
→ SPEC-REVIEWER → CODE-QUALITY-REVIEWER → RECEIVING-CODE-REVIEW 
→ FINAL-REVIEW → WIKI-ARCHIVE
```

## 核心能力

| 能力 | 说明 |
|:-----|:------|
| **22 条质量检测** | 安全/风格/AI slop/过度工程 |
| **7 条硬编码规则** | 不可违反的工程纪律 |
| **10 个 prompt 模板** | 8 角色专用提示词 |
| **可观测性驱动进化** | 自动分析审查趋势 → 进化建议 |
| **多模型路由** | GLM-5.1 → V4 Pro 降级 |
| **批量归纳** | 聚类审查发现 → 提升高频模式 |
| **12 层系统诊断** | Agent 健康度评分 |
| **跨 session 恢复** | 审查失败自动持久化 |

## 架构

```
ecc-engine/
├── ecc/
│   ├── config.py          # 配置模块（路径/模型可配置）
│   ├── core/              # 核心管线
│   │   ├── tri_role.py
│   │   ├── agentshield_check.py
│   │   └── quality_gate.py
│   ├── review/            # 审查引擎
│   │   ├── parallel_review.py
│   │   └── model_router.py
│   ├── evolution/         # 进化回路
│   │   ├── evolution_analyzer.py
│   │   ├── batch_induction.py
│   │   └── ecc_benchmark.py
│   ├── audit/             # 审计工具
│   │   ├── agent_audit.py
│   │   ├── debt_scanner.py
│   │   └── failure_db.py
│   └── instincts/         # 编码本能
│       ├── coding_scout.py
│       └── skill_trainer.py
├── templates/
│   ├── prompts/           # 10 个角色 prompt
│   └── rules/             # 7 条硬规则
├── tests/
│   └── test_ecc_tools.py  # 42 个测试
├── data/                  # 运行时数据（gitignored）
├── pyproject.toml
└── README.md
```

## 环境变量

| 变量 | 默认值 | 说明 |
|:-----|:-------|:------|
| `ECC_HOME` | 包安装目录 | 模板/规则查找路径 |
| `ECC_DATA_DIR` | `{ECC_HOME}/data` | 数据文件目录 |
| `ECC_MODEL_CMD` | `claude --bare --model opus` | 主力模型命令 |
| `ECC_FAST_MODEL_CMD` | `claude --bare --model haiku` | 快速模型命令 |

## 依赖

- Python ≥ 3.11
- `claude` CLI（或其他兼容的命令行 LLM 客户端）
- 标准库：json, re, datetime, pathlib, subprocess
