# ECC Engine — Engineering Control Chain

> **v2.9.x** | MIT License | 独立封装的 Agent 工程编码质量保障体系
> 
> "不是写完代码再检查，而是每一步都有门禁"

---

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 配置模型（可选，默认 claude CLI）
export ECC_MODEL_CMD="claude --bare --model opus"
export ECC_FAST_MODEL_CMD="claude --bare --model haiku"

# 3. 审查代码
ecc-review final-review '审查以下代码: def add(a,b): return a+b'

# 4. 系统诊断
ecc-audit
```

---

## 能力全景

### 🎯 审查管线

| 命令 | 功能 | 说明 |
|:-----|:-----|:------|
| `ecc-review final-review` | 最终审查 | 默认 GLM-5.1(haiku)，V4 Pro 备选 |
| `ecc-review spec-reviewer` | 规范审查 | +Ponytail 5 标签精简审查 |
| `ecc-review engine --mode ultra` | 极端精简模式 | YAGNI 优先 |
| `ecc-parallel` | 并行审查 | SPEC-REVIEWER + CQR 同时运行 |
| `ecc-review --self-eval` | 5 轴质量自评 | 准确性/完整性/清晰度/可操作性/简洁性 |
| `ecc-review --recover` | 跨 session 恢复 | 自动加载上次失败上下文 |

### 📋 22 条质量检测

| 类别 | 数量 | 覆盖 |
|:-----|:----:|:-----|
| 通用编码 | 4 | atomic-write, is-not-none, signal-timeout, re-read-patch |
| 安全 | 7 | secret-detection, path-safety, shell-injection, 等 |
| 风格 | 3 | line-length, trailing-whitespace, todo-left |
| **AI Slop** | **5** | hallucinated-import, dead-code, over-commented, fake-todo, hallucinated-api |
| 过度工程 | 1 | interface/ABC无实现, 工厂单产品, 纯委托包装 |

```bash
ecc-shield file.py                     # 全量检查
ecc-shield --incremental file.py       # 增量检查（仅变更文件）
```

### 🔄 8 角色流水线 + 三桶编排

**Orchestrator 自动分类任务大小，路由到不同工作流：**

```bash
ecc-orch "修复 typo"                   # → small-change (3步快速审查)
ecc-orch "新增因子计算函数"             # → medium-feature (7步标准流水线)
ecc-orch "重构整个回测引擎需跨5模块"    # → large-project (9步含ADR+并行)
```

| 桶 | 适用 | 步骤 |
|:---|:-----|:-----|
| **small-change** | 单文件 ≤30行 | ENGINE → 并行审查 → FINAL-REVIEW |
| **medium-feature** | 单文件 ≥30行 | WRITING-PLANS → ENGINE → 并行审查 → FINAL-REVIEW |
| **large-project** | 跨模块多文件 | ARCH+ADR → REVIEW → WRITING-PLANS → 逐模块ENGINE → 并行审查 → FINAL-REVIEW |

### 📊 进化 + 诊断

| 命令 | 功能 | 输出 |
|:-----|:-----|:------|
| `ecc-evolve` | 审查趋势分析 | 失败率/停滞/cycle time |
| `ecc-audit` | 12 层系统诊断 | 健康评分/关键问题/警告 |
| `ecc-evolve --days 7` | 最近 7 天趋势 | JSON 或表格 |
| `ecc-debt /path/to/code` | 技术债务扫描 | ponytail: 注释清单 |
| `ecc-bench` | 基准测试 | ECC vs baseline 对比 |

### 🔧 可配置项

| 环境变量 | 默认值 | 说明 |
|:---------|:------:|:------|
| `ECC_HOME` | 包目录 | 模板/规则查找路径 |
| `ECC_DATA_DIR` | `{ECC_HOME}/data` | 数据文件目录 |
| `ECC_MODEL_CMD` | `claude --bare --model opus` | 主力模型 |
| `ECC_FAST_MODEL_CMD` | `claude --bare --model haiku` | 快速模型 |
| `ECC_STAGNATION_THRESHOLD` | 0.6 | 停滞检测 Jaccard 阈值 |
| `ECC_STAGNATION_MAX_RETRY` | 2 | 最大重试次数 |
| `ECC_TRAJECTORY_MAX_ROWS` | 500 | 日志最大行数 |

---

## 架构

```
ecc-engine/
├── ecc/
│   ├── config.py              # 配置模块
│   ├── core/                  # 核心管线
│   │   ├── tri_role.py        # 8 角色审查调用器
│   │   ├── agentshield_check.py # 22 条质量检测
│   │   ├── orchestrator.py    # 三桶编排器 (P0)
│   │   ├── quality_gate.py    # 发布前质量门禁
│   │   └── pre_plan_validator.py # 规划验证门
│   ├── review/                # 审查引擎
│   │   ├── parallel_review.py # 并行审查
│   │   └── model_router.py    # 多模型路由+健康检查
│   ├── evolution/             # 进化回路
│   │   ├── evolution_analyzer.py # 可观测性分析
│   │   ├── batch_induction.py # 批量归纳+ML聚类
│   │   └── ecc_benchmark.py   # 基准测试
│   ├── audit/                 # 审计
│   │   ├── agent_audit.py     # 12 层系统诊断
│   │   ├── debt_scanner.py    # 技术债务扫描
│   │   └── failure_db.py      # 跨 session 恢复
│   └── instincts/             # 编码本能
│       ├── coding_scout.py    # 编码模式观察者
│       └── skill_trainer.py   # 技能训练循环
├── templates/
│   ├── prompts/               # 10 个角色 prompt
│   └── rules/                 # 7 条硬规则
├── tests/                     # 35 个单元测试
└── pyproject.toml             # 8 个 CLI 命令
```

---

## 外部借鉴来源

| 来源 | 借鉴内容 | 状态 |
|:-----|:---------|:-----|
| affaan-m/ECC (217K⭐) | 8 角色流水线 | 体系基础 |
| Ponytail (18K⭐) | AGENTS.md + 5标签审查 + 注释 | ✅ 已采纳 |
| codebase-memory-mcp (6K⭐) | 代码知识图谱 | ✅ 已集成 |
| ecc-hermes | 12层诊断 + 5轴自评 | ✅ 已采纳 |
| structural-thinking | Three Buckets + Orchestrator | ✅ 已采纳 |
| Plustar 视频 | 原子化+编排方法 | ✅ 已归档 |
| Philipp Schmid (Google) | Errors as Inputs | ✅ 已采纳 |
| 鱼皮 Vibe Coding | 上下文3层次 | ✅ 已采纳 |

---

## 依赖

- Python ≥ 3.11
- `claude` CLI（或其他兼容的命令行 LLM 客户端）
- 标准库：json, re, datetime, pathlib, subprocess

## 许可证

MIT
