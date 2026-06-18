# 规则 05：FINAL REVIEW 必须用 V4 Pro

## 要求

所有代码变更的 **FINAL REVIEW**（规则 01 的最后一个角色步骤）必须使用 Claude Opus 模型进行审查。

### 命令

```bash
claude --bare --model opus -p "代码审查：<变更描述，约50字>"
```

### 说明

- `--bare` 确保无冗余输出干扰
- `--model opus` 使用 Claude Opus（V4 Pro）进行最严格的审查
- 审查提示词（prompt）约 50 字即可，聚焦于：正确性、安全性、是否违反现有规则
- 审查结果中必须明确标注 **PASS / REVISE / REJECT**

### 约束

- 不可使用其他模型（Haiku / Sonnet / 其他 LLM）替代 V4 Pro 进行终审
- 不可跳过 FINAL REVIEW 步骤直接合入代码
