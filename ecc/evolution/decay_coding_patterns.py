#!/usr/bin/env python3
"""
decay_coding_patterns.py — 编码模式置信度衰减 Cron

读取 coding-patterns.yaml，对超过 60 天未验证的模式进行置信度衰减。
每次衰减 confidence *= 0.85，最低不低于 0.1。

用法:
    python3 decay_coding_patterns.py          # 执行衰减并输出摘要
    python3 decay_coding_patterns.py --dry-run  # 预览衰减结果但不写入
"""

import os, sys, logging
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict

import yaml

# ── 路径 ──
STRATEGIES_DIR = Path(__file__).parent.parent.resolve()
CODING_YAML = STRATEGIES_DIR / "instincts" / "coding-patterns.yaml"

log = logging.getLogger("decay_coding_patterns")

DECAY_FACTOR = 0.85
MIN_CONFIDENCE = 0.1
MAX_AGE_DAYS = 60


def parse_date(d: str) -> date:
    """解析多种日期格式"""
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(d, fmt).date()
        except ValueError:
            continue
    return date.today()


def to_date(val) -> date:
    """将 date 对象或字符串统一转为 date"""
    if isinstance(val, date):
        return val
    return parse_date(str(val))


def should_decay(observation: dict, today: date) -> bool:
    """判断该模式是否需要衰减：last_verified 超 60 天 或 discovered + 60 < today"""
    from datetime import timedelta

    last_verified = observation.get("last_verified")
    discovered = observation.get("discovered")

    if last_verified:
        lv = to_date(last_verified)
        if (today - lv).days > MAX_AGE_DAYS:
            return True

    if discovered:
        disc = to_date(discovered)
        if disc + timedelta(days=MAX_AGE_DAYS) < today:
            return True

    return False


def decay_patterns(dry_run: bool = False) -> List[Dict]:
    """
    执行置信度衰减。

    返回:
        被衰减的观察条目列表（内置了更新后的 confidence）
    """
    if not CODING_YAML.exists():
        log.warning("coding-patterns.yaml 不存在: %s", CODING_YAML)
        return []

    with open(CODING_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    observations = data.get("observations", [])
    today = date.today()
    decayed = []

    for obs in observations:
        if should_decay(obs, today):
            old_conf = obs.get("confidence", 0.7)
            new_conf = max(MIN_CONFIDENCE, round(old_conf * DECAY_FACTOR, 2))
            obs["confidence"] = new_conf
            obs["last_decayed"] = today.isoformat()
            decayed.append(obs)

    if decayed and not dry_run:
        data["observations"] = observations
        data["last_updated"] = today.isoformat()
        tmp_path = CODING_YAML.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp_path.replace(CODING_YAML)

    return decayed


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    dry_run = "--dry-run" in sys.argv

    if not CODING_YAML.exists():
        print(f"❌ coding-patterns.yaml 不存在: {CODING_YAML}")
        sys.exit(1)

    decayed = decay_patterns(dry_run=dry_run)

    if not decayed:
        print("✅ 所有编码模式均为最新，无需衰减")
        return

    print(f"{'🔍 DRY RUN' if dry_run else '📉 衰减执行完毕'}: 共 {len(decayed)} 条模式受影响")
    print()
    for obs in decayed:
        pattern = obs.get("pattern", "?")
        old_conf = obs.get("confidence", 0)
        # Recalculate original confidence before decay for display
        orig_conf = round(old_conf / DECAY_FACTOR, 2) if old_conf > 0 else 0
        discovered = obs.get("discovered", "?")
        last_decayed = obs.get("last_decayed", "?")
        print(f"  {pattern:25s} {orig_conf:.2f} → {old_conf:.2f}  (发现: {discovered}, 衰减: {last_decayed})")


if __name__ == "__main__":
    main()
