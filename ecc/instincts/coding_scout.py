#!/usr/bin/env python3
"""
coding_scout — 编码模式本能侦察兵 (Direction 4)

观察记录什么编码实践有效、什么导致 bug，存入与因子本能同格式的 YAML，
供 instinct_recommender 统一读取（load_all_instincts 已读取所有 .yaml 文件）。

CLI 用法:
    python3 coding_scout.py list               # 列出所有编码模式本能
    python3 coding_scout.py record             # 交互式记录一条新编码模式
    python3 coding_scout.py record --pattern "xxx" --desc "..."   # 非交互式
    python3 coding_scout.py learn              # 从 V4 Pro review 文件自动学习
"""

import os, sys, json, logging, argparse
from pathlib import Path
from datetime import date
from typing import List, Dict, Optional, Any

import yaml

from ecc import config
INSTINCTS_DIR = config.INSTINCTS_DIR
CODING_YAML = config.CODING_PATTERNS_FILE

log = logging.getLogger("coding_scout")


# ═══════════════════════════════════════════════════════════
# 加载 / 保存（复用因子本能相同格式）
# ═══════════════════════════════════════════════════════════

def load_coding_patterns() -> dict:
    """加载 coding-patterns.yaml，返回完整 dict"""
    if not CODING_YAML.exists():
        return {"factor": "coding-patterns", "last_updated": date.today().isoformat(),
                "description": "编码模式本能", "observations": []}
    try:
        with open(CODING_YAML, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("无法解析 %s: %s", CODING_YAML, exc)
        return {"factor": "coding-patterns", "last_updated": date.today().isoformat(),
                "description": "编码模式本能", "observations": []}


def save_coding_patterns(data: dict) -> Path:
    """原子写入 coding-patterns.yaml"""
    INSTINCTS_DIR.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = date.today().isoformat()
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    tmp_path = CODING_YAML.with_suffix(".yaml.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(CODING_YAML)
    return CODING_YAML


# ═══════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════

def record_coding_pattern(
    pattern: str,
    description: str,
    benefit: str = "",
    trigger: str = "",
    fix_effort_hours: float = 0.0,
    confidence: float = 0.7,
    frequency: str = "always",
) -> dict:
    """记录一条编码模式本能观察

    参数:
        pattern:         模式名称（如 atomic-write）
        description:     模式描述（怎么做）
        benefit:         好处（防止什么 bug）
        trigger:         发现触发条件（什么 review / 事故）
        fix_effort_hours:修复耗时（小时）
        confidence:      置信度 (0~1)
        frequency:       频率描述（always / sometimes / rarely）

    返回:
        新创建的观察字典
    """
    obs = {
        "pattern": pattern,
        "description": description,
        "benefit": benefit,
        "discovered": date.today().isoformat(),
        "trigger": trigger,
        "fix_effort_hours": round(fix_effort_hours, 2),
        "confidence": round(confidence, 2),
        "frequency": frequency,
    }

    data = load_coding_patterns()
    existing = data.get("observations", [])

    # 去重：同 pattern 则融合（累加置信度 + 更新记录）
    for i, e in enumerate(existing):
        if e.get("pattern") == pattern:
            old_conf = e.get("confidence", 0.0)
            new_conf = min(1.0, old_conf + 0.1)  # 每次印证 +0.1
            obs["confidence"] = round(new_conf, 2)
            obs["discovered"] = e.get("discovered", obs["discovered"])
            obs["fix_effort_hours"] = round((e.get("fix_effort_hours", 0) + fix_effort_hours) / 2, 2)
            existing[i] = obs
            data["observations"] = existing
            save_coding_patterns(data)
            log.info("编码模式 '%s' 已更新 (置信度 %.2f)", pattern, new_conf)
            return obs

    # 新模式：追加
    existing.append(obs)
    data["observations"] = existing
    save_coding_patterns(data)
    log.info("编码模式 '%s' 已记录 (置信度 %.2f)", pattern, confidence)
    return obs


def learn_from_v4pro_review(findings_path: Optional[str] = None) -> int:
    """从 V4 Pro review 输出文件自动生成编码模式本能

    扫描 findings_file 中符合固定格式的行，自动创建 instinct 条目。
    格式示例（每行一条）:
        pattern=atomic-write desc=文件写入必须.tmp→os.replace benefit=防止文件损坏 ...

    参数:
        findings_path: V4 Pro 审核结果文件路径。None 时从 stdin 读取。

    返回:
        成功创建的条目数
    """
    import shlex

    if findings_path:
        src = open(findings_path, "r", encoding="utf-8")
    else:
        log.info("从 stdin 读取 V4 Pro 发现...")
        src = sys.stdin

    count = 0
    with src as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("pattern="):
                continue

            try:
                tokens = shlex.split(line)
                kwargs = {}
                for t in tokens:
                    if "=" in t:
                        key, val = t.split("=", 1)
                        kwargs[key] = val

                pattern = kwargs.pop("pattern", None)
                if not pattern:
                    continue

                desc = kwargs.pop("desc", "")
                benefit = kwargs.pop("benefit", "")
                trigger = kwargs.pop("trigger", "")
                fix_effort = float(kwargs.pop("fix_effort", kwargs.pop("fix_effort_hours", 0)))
                confidence = float(kwargs.pop("confidence", 0.7))
                frequency = kwargs.pop("frequency", "always")

                record_coding_pattern(
                    pattern=pattern,
                    description=desc,
                    benefit=benefit,
                    trigger=trigger,
                    fix_effort_hours=fix_effort,
                    confidence=confidence,
                    frequency=frequency,
                )
                count += 1
            except Exception as exc:
                log.warning("跳过行 '%s': %s", line[:60], exc)

    return count


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def cmd_list():
    """列出所有编码模式本能"""
    data = load_coding_patterns()
    obs = data.get("observations", [])
    if not obs:
        print("📭 暂无编码模式记录")
        return

    print(f"🧠 编码模式本能 ({data.get('factor', 'coding-patterns')})")
    print(f"   描述: {data.get('description', '')}")
    print(f"   更新: {data.get('last_updated', '')}")
    print(f"   共 {len(obs)} 条记录\n")

    # 按置信度排序
    sorted_obs = sorted(obs, key=lambda x: x.get("confidence", 0), reverse=True)
    for i, o in enumerate(sorted_obs, 1):
        p = o.get("pattern", "?")
        desc = o.get("description", "")
        benefit = o.get("benefit", "")
        conf = o.get("confidence", 0)
        hours = o.get("fix_effort_hours", 0)
        trig = o.get("trigger", "")
        freq = o.get("frequency", "")
        discovered = o.get("discovered", "")

        bar = "🟢" if conf >= 0.8 else "🟡" if conf >= 0.5 else "🔴"
        print(f"  {bar} #{i} {p}")
        print(f"     描述:   {desc}")
        if benefit:
            print(f"     收益:   {benefit}")
        if trig:
            print(f"     触发:   {trig}")
        print(f"     置信度: {conf:.2f}  耗时: {hours:.1f}h  频率: {freq}  发现: {discovered}")
        print()


def cmd_record(args: argparse.Namespace):
    """交互式或非交互式记录一条新模式"""
    pattern = args.pattern
    desc = args.desc
    benefit = args.benefit or ""
    trigger = args.trigger or ""
    fix_effort = args.fix_effort or 0.0
    confidence = args.confidence or 0.7
    frequency = args.frequency or "always"

    is_interactive = not (pattern and desc)

    if is_interactive:
        # 交互式补全
        if not pattern:
            pattern = input("模式名称 (如 atomic-write): ").strip()
        if not desc:
            desc = input("模式描述: ").strip()
        if not benefit:
            benefit = input("收益 (防止什么): ").strip()
        if not trigger:
            trigger = input("触发条件 (可选): ").strip()
        if not fix_effort:
            try:
                fix_effort = float(input("修复耗时 (小时, 默认 0.5): ") or "0.5")
            except ValueError:
                fix_effort = 0.5
        if not args.confidence:
            try:
                confidence = float(input(f"置信度 (0~1, 默认 {confidence}): ") or str(confidence))
            except ValueError:
                pass
        if not args.frequency:
            freq_in = input("频率 (always/sometimes/rarely, 默认 always): ").strip()
            if freq_in:
                frequency = freq_in

    if not pattern or not desc:
        print("❌ 模式名称和描述为必填")
        return

    obs = record_coding_pattern(
        pattern=pattern,
        description=desc,
        benefit=benefit,
        trigger=trigger,
        fix_effort_hours=fix_effort,
        confidence=confidence,
        frequency=frequency,
    )
    print(f"✅ 已记录编码模式: {obs['pattern']} (置信度 {obs['confidence']:.2f})")


def cmd_learn(args: argparse.Namespace):
    """从 V4 Pro review 文件自动学习"""
    findings_path = args.findings
    count = learn_from_v4pro_review(findings_path)
    if count > 0:
        print(f"✅ 已从 V4 Pro review 学习 {count} 条编码模式")
    else:
        print("⚠️  未发现新的编码模式。检查输入文件格式是否正确。")
        print("   格式: pattern=xxx desc=... benefit=... trigger=... confidence=...")


def main():
    parser = argparse.ArgumentParser(
        description="coding_scout — 编码模式本能侦察兵",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="cmd", help="子命令")

    # list
    subparsers.add_parser("list", help="列出所有编码模式本能")

    # record
    record_p = subparsers.add_parser("record", help="记录一条新编码模式")
    record_p.add_argument("--pattern", default="", help="模式名称")
    record_p.add_argument("--desc", default="", help="模式描述")
    record_p.add_argument("--benefit", default="", help="收益")
    record_p.add_argument("--trigger", default="", help="触发条件")
    record_p.add_argument("--fix-effort", type=float, default=0.0, help="修复耗时 (小时)")
    record_p.add_argument("--confidence", type=float, default=0.0, help="置信度 (0~1)")
    record_p.add_argument("--frequency", default="", help="频率")

    # learn
    learn_p = subparsers.add_parser("learn", help="从 V4 Pro review 文件自动学习")
    learn_p.add_argument("--findings", default=None, help="V4 Pro 审核结果文件路径")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "record":
        cmd_record(args)
    elif args.cmd == "learn":
        cmd_learn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
