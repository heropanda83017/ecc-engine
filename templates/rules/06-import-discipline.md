# 规则 06：不引入未声明第三方库

## 要求

1. 所有外部依赖必须显式声明在项目根目录的 `requirements.txt` 中
2. 未经声明的第三方库禁止在代码中 import 使用
3. 允许直接 import 的 Python 标准库模块：

| 模块 | 用途 |
|------|------|
| `pathlib` | 路径操作 |
| `json` | JSON 编解码 |
| `re` | 正则匹配 |
| `logging` | 日志记录 |
| `datetime` | 日期时间处理 |
| `typing` | 类型注解 |
| `os` | 操作系统接口 |

4. 允许在 `requirements.txt` 中声明后使用的常用第三方库：

| 库 | 用途 |
|----|------|
| `pandas` | 数据处理 |
| `numpy` | 数值计算 |
| `yaml` (PyYAML) | YAML 配置解析（如已安装） |

5. 新增任何第三方依赖必须先更新 `requirements.txt`，再在代码中 import

## 示例

```python
# ✅ 允许（标准库）
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

# ✅ 允许（已声明在 requirements.txt 中）
import pandas as pd
import numpy as np
import yaml

# ❌ 禁止（未声明）
import requests           # 除非在 requirements.txt 中
from sqlalchemy import *  # 除非在 requirements.txt 中
```
