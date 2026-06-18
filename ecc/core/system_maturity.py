#!/usr/bin/env python3
"""system_maturity.py — 系统成熟度检测 (学习率衰减组件)

系统越成熟, 修改门槛越高:
  explore(≤100 session)  → promote_threshold=2, 任意修改
  growth(≤300 session)   → promote_threshold=3, 需REVIEW
  mature(>300 session)   → promote_threshold=5, 核心组件LOCKED

对标: 深度学习三件套之「学习率衰减」
  原文: "初始阶段宽松阈值 → 成熟阶段提高频次阈值 → 锁定阶段核心规则LOCKED"
"""

from pathlib import Path

# 核心组件列表 (LOCKED: 修改需人工确认)
LOCKED_MODULES = [
    "strategies/degradation_harness.py",
    "strategies/governance_guard.py",
    "strategies/freshness_tracker.py",
    "strategies/confidence_scorer.py",
    "strategies/pipeline_sentinel.py",
    "strategies/state_manager.py",
    "strategies/__init__.py",
]

# 成熟阶段定义
STAGES = {
    "explore": {"max_sessions": 100, "promote_threshold": 2, "locked": False},
    "growth":  {"max_sessions": 300, "promote_threshold": 3, "locked": False},
    "mature":  {"max_sessions": None, "promote_threshold": 5, "locked": True},
}

# 投资引擎根目录
IE_ROOT = Path("/mnt/e/AIGC-KB/investment-engine")


class SystemMaturity:
    """系统成熟度检测."""

    @staticmethod
    def current_stage() -> str:
        """返回当前阶段: explore/growth/mature."""
        session_count = SystemMaturity._count_sessions()
        for stage, config in STAGES.items():
            max_s = config["max_sessions"]
            if max_s is None or session_count <= max_s:
                return stage
        return "mature"

    @staticmethod
    def promote_threshold() -> int:
        """返回当前阶段的 skill 升级阈值."""
        stage = SystemMaturity.current_stage()
        return STAGES[stage]["promote_threshold"]

    @staticmethod
    def is_locked(filepath: str) -> bool:
        """检查文件是否被 LOCKED."""
        # 支持相对路径和绝对路径
        for locked in LOCKED_MODULES:
            if filepath.endswith(locked):
                return True
        return False

    @staticmethod
    def stage_config() -> dict:
        """返回当前阶段的完整配置."""
        stage = SystemMaturity.current_stage()
        return {"stage": stage, **STAGES[stage]}

    @staticmethod
    def _count_sessions() -> int:
        """估算 session 总数 (从 tri_role 的 trajectory 日志)."""
        try:
            from pathlib import Path
            base = Path.home() / ".hermes" / "profiles" / "ai-investor" / "data"
            traj = base / "trajectory.jsonl"
            if traj.exists():
                count = sum(1 for _ in traj.read_text(encoding="utf-8").splitlines() if _.strip())
                return max(count, 436)  # 至少已知的436
        except Exception:
            pass
        return 436  # 已知最小值


if __name__ == "__main__":
    s = SystemMaturity()
    cfg = s.stage_config()
    print(f"当前阶段: {cfg['stage']}")
    print(f"promote_threshold: {cfg['promote_threshold']}")
    print(f"LOCKED: {cfg['locked']}")
    print(f"session_count: {s._count_sessions()}")
    print()
    print("LOCKED 模块:")
    for m in LOCKED_MODULES:
        print(f"  🔒 {m}")
