#!/usr/bin/env python3
"""
failure_db.py — 跨 session 状态恢复数据库

持久化 tri_role 审查失败记录到 failure_db.jsonl，
下次 session 启动时自动加载最近失败记录并提供恢复上下文。

用法:
    from failure_db import FailureDB
    db = FailureDB()
    db.record("final-review", "session-123", "上下文摘要", "失败原因")
    last = db.get_last("final-review")
    summary = db.get_session_summary()
"""

import json, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from ecc import config
FAILURE_DB_FILE = config.FAILURE_DB_FILE


class FailureDB:
    """跨 session 失败数据库"""

    def __init__(self):
        self._cache = None

    def _ensure_file(self):
        FAILURE_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

    def record(self, role: str, session_id: str, context: str, reason: str,
               verdict: str = "fail"):
        """记录一次失败"""
        self._ensure_file()
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id or "unknown",
            "role": role,
            "verdict": verdict,
            "context": context[:200],
            "reason": reason[:200],
        }
        try:
            with open(FAILURE_DB_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def get_last(self, role: str = None, limit: int = 5) -> list[dict]:
        """获取最近的失败记录"""
        if not FAILURE_DB_FILE.exists():
            return []

        entries = []
        try:
            with open(FAILURE_DB_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if role is None or d.get("role") == role:
                            entries.append(d)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []

        entries.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return entries[:limit]

    def get_session_summary(self, session_id: str = None) -> str:
        """生成跨 session 失败摘要（用于 session 启动注入）"""
        entries = self.get_last(limit=10)

        if not entries:
            return ""

        # 按角色统计
        from collections import Counter
        role_counts = Counter(e.get("role", "unknown") for e in entries)
        recent = entries[:3]

        lines = [
            "## 🔄 跨 Session 状态恢复",
            f"\n上次 session 失败统计:",
        ]
        for role, count in role_counts.most_common():
            lines.append(f"- {role}: {count} 次失败")
        lines.append(f"\n最近失败:")
        for e in recent:
            lines.append(f"- [{e.get('ts','?')[:10]}] {e.get('role','?')}: {e.get('reason','?')[:60]}")
        lines.append("")

        return "\n".join(lines)

    def recovery_context(self, role: str) -> Optional[str]:
        """为指定角色生成恢复上下文"""
        last = self.get_last(role=role, limit=1)
        if not last:
            return None

        e = last[0]
        return (
            f"[跨 session 恢复] 上次 {role} 审查失败\n"
            f"  时间: {e.get('ts', '?')}\n"
            f"  会话: {e.get('session_id', '?')}\n"
            f"  原因: {e.get('reason', '?')}\n"
            f"  上下文: {e.get('context', '?')}\n"
        )


if __name__ == "__main__":
    import sys
    db = FailureDB()
    if len(sys.argv) >= 3 and sys.argv[1] == "record":
        db.record(sys.argv[2], os.environ.get("HERMES_SESSION_ID", "cli"),
                  sys.argv[3] if len(sys.argv) > 3 else "",
                  sys.argv[4] if len(sys.argv) > 4 else "")
        print(f"✅ 已记录: {sys.argv[2]}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "summary":
        print(db.get_session_summary())
    elif len(sys.argv) >= 2 and sys.argv[1] == "recent":
        role = sys.argv[2] if len(sys.argv) > 2 else None
        entries = db.get_last(role=role)
        for e in entries:
            print(f"[{e.get('ts','?')[:10]}] {e.get('role','?')}: {e.get('reason','?')[:80]}")
    else:
        print("用法:")
        print("  python3 failure_db.py record <role> [context] [reason]")
        print("  python3 failure_db.py summary")
        print("  python3 failure_db.py recent [role]")
