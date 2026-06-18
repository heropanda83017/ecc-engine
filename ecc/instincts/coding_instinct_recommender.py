#!/usr/bin/env python3
"""
coding_instinct_recommender.py — 编码模式本能推荐器（D4 最后一公里）

从 instincts/coding-patterns.yaml 读取编码模式本能，
只输出高置信度（>0.7）的编码约束，供 REVIEW/FINAL-REVIEW 阶段自动引用。

用法：
    python3 coding_instinct_recommender.py          # 输出推荐
    python3 coding_instinct_recommender.py --rules  # 输出为 markdown
    python3 coding_instinct_recommender.py --threshold 0.8  # 自定义置信度阈值
"""

import sys, yaml
from pathlib import Path
from datetime import date

BASE = Path(__file__).parent.parent.resolve()  # ai-investor/
INSTINCTS_DIR = BASE / "instincts"
CODING_FILE = INSTINCTS_DIR / "coding-patterns.yaml"

def load():
    if not CODING_FILE.exists():
        return []
    data = yaml.safe_load(CODING_FILE.read_text(encoding="utf-8"))
    return data.get("observations", []) if data else []

def recommend(threshold: float = 0.7, output: str = "text"):
    obs = load()
    filtered = [p for p in obs if p.get("confidence", 0) >= threshold]
    filtered.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    if output == "rules":
        for p in filtered:
            desc = p.get("description", "")
            benefit = p.get("benefit", "")
            conf = p.get("confidence", 0)
            print(f"- **{p['pattern']}** (conf={conf:.2f}): {desc}. {benefit}")
        return

    if not filtered:
        print(f"无高置信度编码模式 (threshold={threshold})")
        return

    print(f"编码模式本能推荐 ({date.today().isoformat()})")
    print(f"阈值: ≥{threshold}")
    print()
    for p in filtered:
        conf = p.get("confidence", 0)
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        print(f"  {bar} {p['pattern']:25s} {p.get('description','')}")
        print(f"    benefit: {p.get('benefit','')}")
        trigger = p.get('trigger', '')
        if trigger:
            print(f"    trigger: {trigger}")

if __name__ == '__main__':
    threshold = 0.7
    output = "text"
    args = [a for a in sys.argv[1:] if not a.startswith('--threshold')]
    for i, a in enumerate(sys.argv[1:]):
        if a == '--rules':
            output = 'rules'
        elif a == '--threshold' and i+2 < len(sys.argv):
            try: threshold = float(sys.argv[i+2])
            except: pass
    recommend(threshold, output)
