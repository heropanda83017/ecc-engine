# 规则 02：统一使用 pip/venv，禁止 conda

## 原因

Conda 与当前 WSL/Windows 混合环境存在以下不兼容问题：

- Conda 环境激活后在跨 WSL 文件系统（`/mnt/`）操作时容易出现 PATH 解析异常
- Conda 的包解析与 pip 的 `requirements.txt` 冲突，导致依赖锁定困难
- Conda 安装的二进制包（如 numpy、pandas）在 WSL 内核上偶发 ABI 不兼容

## 要求

- 所有项目的 Python 依赖管理统一使用 `pip` + `venv`
- 禁止安装或使用 conda / miniconda / anaconda
- 虚拟环境统一创建在项目根目录下的 `.venv/` 中
- 依赖清单统一写入 `requirements.txt`，并保持版本锁定
