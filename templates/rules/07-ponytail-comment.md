# 规则 07：Ponytail 注释约定 — 每个简化标注天花板与升级路径

**生效日期：2026-06-17（强制规则，无例外）**

## 原则

每个故意简化的代码位置必须加 `ponytail:` 注释，标注：
1. **天花板** — 当前简化方案在什么条件下会失效
2. **升级路径** — 什么时候/什么条件下需要换方案

## 格式

```python
# ponytail: <天花板>, <升级路径>
```

## 示例

```python
# ponytail: global lock, per-account locks if throughput matters
# ponytail: O(n²) scan, switch to hash index if dataset > 10K rows
# ponytail: naive heuristic, replace with ML model when labeled data available
# ponytail: single-threaded, add threading when I/O becomes bottleneck
```

## 什么时候必须加

- 任何故意跳过"最佳实践"而选择更简单方案的地方
- 任何已知有性能/安全/可扩展性上限的简化
- 任何"先这样，以后再说"的代码

## 什么时候不需要加

- 一行就能说清楚的 trivial 代码
- 标准库/平台原生功能的直接使用
- 用户明确要求的简化

## 关联

- `scripts/debt_scanner.py` — 自动扫描所有 `ponytail:` 注释生成债务清单
- `scripts/agentshield_check.py` — C16 检查缺少 `ponytail:` 注释的过度简化
