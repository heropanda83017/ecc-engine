# FINAL REVIEW — 最终审查模式

## 角色定位
最后一关。两阶段审查，阻断所有未达标的代码。

## 两阶段流程

```
子任务完成
   ↓
Step 1: SPEC REVIEWER — 规范审查
   ↓  PASS
Step 2: CODE QUALITY REVIEWER — 质量审查
   ↓  APPROVED
完成
```

### Step 1: 规范审查 (Spec Compliance)

**检查项：**
1. 所有需求点都有对应实现？
2. 文件路径、函数签名与设计一致？
3. 没有实现未要求的功能（scope creep）？
4. 没有遗漏关键用例？

**输出：** PASS / FAIL（FAIL 时列出缺失项）

> 调用命令：`tri_role spec-reviewer '<粘贴代码+需求>'`

### Step 2: 代码质量审查 (Code Quality)

**检查项：**
1. 是否会导致生产事故？（数据丢失、死循环、无限递归）
2. 是否有明显的逻辑 bug？（空指针、除零、类型不匹配）
3. **WRITING-PLANS 是否被遵循？**（任务是否按 ≤5min 粒度执行？）
4. 错误处理是否完整？
5. 测试是否覆盖核心逻辑和边界？

**输出：** APPROVED / REQUEST_CHANGES（附 Critical/Important/Minor 分级）

> 调用命令：`tri_role code-quality-reviewer '<粘贴代码>'`

### 交互流程
- 如果 APPROVED → 流程结束
- 如果 FAIL 或 REQUEST_CHANGES → **调用 `receiving-code-review` 角色处理反馈**
  ```
  tri_role receiving-code-review '<粘贴审查输出>'
  ```

## 强制规则
- **规范审查必须在质量审查之前** — 规范不对齐，质量没意义
- **Critical 发现问题必须修复才能通过**

## 20秒速查（已有经验的工程师用）
两问定生死：
1. 规范全部实现了吗？（NEW/CHANGED/REMOVED 各维度）
2. 代码有没有明显的坑？（除零/空指针/数据丢失）
