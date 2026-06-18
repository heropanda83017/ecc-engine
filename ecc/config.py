"""ECC Engine — 配置模块

替换所有硬编码路径，支持通过环境变量或配置文件自定义。
"""

import os
from pathlib import Path

# ── 默认路径 ──
# 可通过环境变量 ECC_HOME 覆盖
_ECC_HOME = Path(os.environ.get("ECC_HOME", str(Path(__file__).parent.parent.resolve())))

# ── 数据目录 ──
DATA_DIR = Path(os.environ.get("ECC_DATA_DIR", str(_ECC_HOME / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── 模板目录 ──
TEMPLATES_DIR = _ECC_HOME / "templates" / "prompts"
RULES_DIR = _ECC_HOME / "templates" / "rules"
INSTINCTS_DIR = _ECC_HOME / "ecc" / "instincts"

# ── 数据文件 ──
TRACES_FILE = DATA_DIR / "traces_v2.jsonl"
TRAJECTORY_FILE = DATA_DIR / "trajectory.jsonl"
FAILURE_DB_FILE = DATA_DIR / "failure_db.jsonl"
BENCHMARK_FILE = DATA_DIR / "benchmark_results.json"
CODING_PATTERNS_FILE = INSTINCTS_DIR / "coding-patterns.yaml"

# ── 模型配置 ──
# 默认模型命令，可通过环境变量覆盖
# 格式: ["claude", "--bare", "--model", "opus"]
DEFAULT_MODEL_CMD = os.environ.get(
    "ECC_MODEL_CMD",
    "claude --bare --model opus"
).split()

FAST_MODEL_CMD = os.environ.get(
    "ECC_FAST_MODEL_CMD",
    "claude --bare --model haiku"
).split()

# ── Cron 输出目录 ──
CRON_OUTPUT = _ECC_HOME / "cron" / "output"
CRON_OUTPUT.mkdir(parents=True, exist_ok=True)

# ── 缓冲目录 ──
BUFFER_DIR = DATA_DIR / "batch_buffer"
BUFFER_DIR.mkdir(parents=True, exist_ok=True)


def ensure_dirs():
    """确保所有数据目录存在"""
    for d in [DATA_DIR, CRON_OUTPUT, BUFFER_DIR]:
        d.mkdir(parents=True, exist_ok=True)
