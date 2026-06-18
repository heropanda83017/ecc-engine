# 角色专用 Prompt 模板库

每个角色加载仅需要的工具集，避免冗余上下文。

终端调用时从此目录加载对应模板而非内联构造。

## 模板列表

| 文件            | 角色          | 用途               |
|-----------------|---------------|--------------------|
| ARCH.md         | 架构师        | 系统设计、组件边界  |
| REVIEW.md       | 审查者        | 代码审查、找 bug    |
| ENGINE.md       | 工程师        | 编码实现            |
| FINAL-REVIEW.md | 最终审查者    | 上线前最后关卡      |

## 使用

```python
from loader import load_prompt

prompt = load_prompt('arch')    # ARCH.md
prompt = load_prompt('review')  # REVIEW.md
prompt = load_prompt('engine')  # ENGINE.md
prompt = load_prompt('final-review')  # FINAL-REVIEW.md
```
