# 规则 03：守卫函数必须 fail-soft

## 原则

所有守卫函数（guard functions），包括但不限于 `check_pre_data`、`check_post_data`、`check_connectivity`、`check_schema` 等，必须遵循 **fail-soft** 设计：

- 不允许抛出未捕获的异常导致流水线（pipeline）崩溃
- 不允许在守卫函数内部调用 `sys.exit()` 或 `os._exit()`
- 所有非致命错误必须记录警告日志后继续执行

## GuardResult 模式

所有守卫函数必须返回统一的 `GuardResult` 三元组：

```python
# (ok: bool, msg: str, details: dict)
# ok=True   → 检查通过
# ok=False  → 检查失败，但流水线继续
```

### 示例

```python
def check_pre_data(ctx) -> tuple[bool, str, dict]:
    try:
        data = load_data(ctx.input_path)
        if data is None:
            return False, "预加载数据为空，跳过本轮", {"path": ctx.input_path, "records": 0}
        return True, f"预加载数据成功，{len(data)} 条记录", {"records": len(data)}
    except Exception as e:
        logging.warning("check_pre_data 异常（fail-soft）: %s", e)
        return False, f"预加载数据异常: {e}", {"error": str(e)}
```
