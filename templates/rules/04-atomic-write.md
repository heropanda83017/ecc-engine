# 规则 04：文件写入必须原子操作

## 原因

直接写入目标路径在写入中途发生崩溃或中断时，会产生残缺文件（partial file），导致下游读取时出现解析错误或脏数据。

## 要求

所有文件写入操作必须使用 **原子写入模式**：

1. 先将内容写入一个 **临时文件**（同一目录下，后缀 `.tmp`）
2. 使用 `os.replace()` 将临时文件原子地覆盖到目标路径

### 标准模式

```python
import os
import tempfile

def atomic_write(target_path: str, content: str) -> None:
    """原子写入文件内容。"""
    tmp_path = target_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)
```

### 约束

- 禁止直接使用 `open(target_path, "w")` 或 `Path.write_text()`
- 临时文件和目标文件必须在 **同一文件系统**（同一磁盘分区）内
- 写入完成后务必调用 `os.fsync()` 确保数据落盘
