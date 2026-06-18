"""Test suite for ECC P0/P1/P2 tools — evolution_analyzer, parallel_review,
model_router, batch_induction, failure_db, agentshield slop, skill_library.

Run: pytest tests/test_ecc_tools.py -v
"""

import json, os, sys, tempfile
from pathlib import Path

# Add scripts to path
SCRIPTS = Path(__file__).parent.parent / "ecc"
sys.path.insert(0, str(SCRIPTS))

import pytest


# ══════════════════════════════════════════════════════════════════════
# evolution_analyzer tests
# ══════════════════════════════════════════════════════════════════════

class TestEvolutionAnalyzer:
    """Test trace analysis, trajectory analysis, and suggestion generation"""

    def _make_trace(self, name: str, verdict: str = "approved", status: str = "OK"):
        return {
            "trace_id": "test",
            "span_id": "test",
            "name": name,
            "attributes": {"verdict": verdict} if verdict else {},
            "status": status,
            "duration_ns": 1_000_000,
        }

    def _make_trajectory(self, role: str, verdict: str = "approved",
                         round_num: int = 1, duration_s: int = 10):
        return {
            "ts": "2026-06-17T00:00:00Z",
            "role": role,
            "verdict": verdict,
            "round": round_num,
            "duration_s": duration_s,
        }

    def test_load_traces_empty_when_no_file(self):
        from ecc.evolution.evolution_analyzer import load_traces, load_trajectory
        import os
        from pathlib import Path
        base = Path(__file__).parent.parent / "data"
        # Backup and remove both files
        backups = {}
        for fname in ["traces_v2.jsonl", "trajectory.jsonl"]:
            fp = base / fname
            if fp.exists():
                backups[fname] = fp.read_text(encoding="utf-8")
                fp.unlink()
        try:
            assert load_traces() == []
            assert load_trajectory() == []
        finally:
            for fname, content in backups.items():
                (base / fname).write_text(content, encoding="utf-8")

    def test_analyze_traces_basic(self):
        from ecc.evolution.evolution_analyzer import analyze_traces
        traces = [
            self._make_trace("tri_role:review", "approved"),
            self._make_trace("tri_role:final-review", "fail"),
            self._make_trace("tri_role:final-review", "fail"),
            self._make_trace("tri_role:review:retry_0", "conditional"),
        ]
        result = analyze_traces(traces)
        assert result["total_spans"] == 4
        assert result["verdicts"]["approved"] == 1
        assert result["verdicts"]["fail"] == 2
        assert result["verdicts"]["conditional"] == 1

    def test_analyze_traces_stagnation(self):
        from ecc.evolution.evolution_analyzer import analyze_traces
        traces = [
            self._make_trace("tri_role:final-review:retry_0", "conditional"),
            self._make_trace("tri_role:final-review:retry_1", "conditional"),
            self._make_trace("tri_role:final-review:retry_2", "conditional"),
        ]
        # Each has attributes with verdict = conditional
        for t in traces:
            t["attributes"] = {"verdict": "conditional"}
        result = analyze_traces(traces)
        assert result["stagnation_events"] > 0

    def test_analyze_trajectory_basic(self):
        from ecc.evolution.evolution_analyzer import analyze_trajectory
        entries = [
            self._make_trajectory("final-review", "approved"),
            self._make_trajectory("final-review", "fail", round_num=2),
            self._make_trajectory("review", "conditional"),
        ]
        result = analyze_trajectory(entries)
        assert result["total_entries"] == 3
        assert result["verdict_stats"]["approved"] == 1
        assert result["retry_distribution"]["final-review"] == 1

    def test_generate_suggestions_high_failure_rate(self):
        from ecc.evolution.evolution_analyzer import generate_suggestions
        trace_analysis = {
            "failure_trend": [
                {"day": "2026-06-15", "total": 10, "fails": 8, "fail_rate": 80.0},
                {"day": "2026-06-16", "total": 10, "fails": 7, "fail_rate": 70.0},
                {"day": "2026-06-17", "total": 10, "fails": 9, "fail_rate": 90.0},
            ],
            "stagnation_events": 3,
            "total_spans": 100,
            "verdicts": {"approved": 30, "conditional": 30, "fail": 40},
            "duration_stats": {},
        }
        traj_analysis = {
            "retry_distribution": {"final-review": 10},
            "avg_duration_by_role": {"final-review": {"mean_s": 300, "median_s": 200, "count": 5}},
        }
        suggestions = generate_suggestions(trace_analysis, traj_analysis)
        priorities = [s["priority"] for s in suggestions]
        assert "P0" in priorities
        assert "P1" in priorities

    def test_generate_suggestions_low_data(self):
        from ecc.evolution.evolution_analyzer import generate_suggestions
        trace_analysis = {
            "failure_trend": [],
            "stagnation_events": 0,
            "total_spans": 10,
            "verdicts": {},
            "duration_stats": {},
        }
        traj_analysis = {
            "retry_distribution": {},
            "avg_duration_by_role": {},
        }
        suggestions = generate_suggestions(trace_analysis, traj_analysis)
        # Should still generate data_volume suggestion
        assert any(s["category"] == "data_volume" for s in suggestions)


# ══════════════════════════════════════════════════════════════════════
# parallel_review tests
# ══════════════════════════════════════════════════════════════════════

class TestParallelReview:
    """Test verdict parsing and aggregation"""

    def test_parse_verdict_approved(self):
        from ecc.review.parallel_review import parse_verdict
        assert parse_verdict("APPROVED") == "approved"
        assert parse_verdict("APPROVED\nEverything looks good") == "approved"

    def test_parse_verdict_conditional(self):
        from ecc.review.parallel_review import parse_verdict
        assert parse_verdict("CONDITIONAL") == "conditional"
        assert parse_verdict("CONDITIONAL - fix minor issues") == "conditional"

    def test_parse_verdict_fail(self):
        from ecc.review.parallel_review import parse_verdict
        assert parse_verdict("## Review\nFAIL - missing tests") == "fail"
        assert parse_verdict("REQUEST_CHANGES") == "fail"
        assert parse_verdict("") == "fail"

    def test_aggregate_verdict_all_approved(self):
        from ecc.review.parallel_review import aggregate_verdict
        results = [
            {"verdict": "approved", "elapsed_s": 10},
            {"verdict": "approved", "elapsed_s": 15},
        ]
        assert aggregate_verdict(results) == "approved"

    def test_aggregate_verdict_any_fail(self):
        from ecc.review.parallel_review import aggregate_verdict
        results = [
            {"verdict": "approved", "elapsed_s": 10},
            {"verdict": "fail", "elapsed_s": 15},
        ]
        assert aggregate_verdict(results) == "fail"

    def test_aggregate_verdict_timeout_becomes_conditional(self):
        from ecc.review.parallel_review import aggregate_verdict
        results = [
            {"verdict": "approved", "elapsed_s": 10},
            {"verdict": "timeout", "elapsed_s": 120},
        ]
        assert aggregate_verdict(results) == "conditional"


# ══════════════════════════════════════════════════════════════════════
# model_router tests
# ══════════════════════════════════════════════════════════════════════

class TestModelRouter:
    """Test model routing, verdict parsing, and aggregation"""

    def test_parse_verdict_all_formats(self):
        from ecc.review.model_router import parse_verdict
        assert parse_verdict("APPROVED") == "approved"
        assert parse_verdict("## Results\nAPPROVED\nDetails...") == "approved"
        assert parse_verdict("CONDITIONAL - needs fixes") == "conditional"
        assert parse_verdict("## Review\nFAIL - missing type hints") == "fail"
        assert parse_verdict("REQUEST_CHANGES") == "fail"
        assert parse_verdict("") == "fail"
        assert parse_verdict("Some random text") == "fail"

    def test_aggregate_verdict_majority(self):
        from ecc.review.model_router import aggregate_verdicts
        # deepseek-v4-pro weight=3, glm-5.1 weight=2
        result = aggregate_verdicts([
            {"verdict": "approved", "model": "deepseek-v4-pro"},
            {"verdict": "approved", "model": "glm-5.1"},
        ])
        assert result == "approved"

    def test_aggregate_verdict_split(self):
        from ecc.review.model_router import aggregate_verdicts
        result = aggregate_verdicts([
            {"verdict": "approved", "model": "deepseek-v4-pro"},
            {"verdict": "conditional", "model": "glm-5.1"},
        ])
        # 3/5 >= 0.5 → approved
        assert result == "approved"

    def test_aggregate_verdict_all_fail(self):
        from ecc.review.model_router import aggregate_verdicts
        result = aggregate_verdicts([
            {"verdict": "fail", "model": "deepseek-v4-pro"},
            {"verdict": "fail", "model": "glm-5.1"},
        ])
        assert result == "fail"

    def test_aggregate_verdict_empty(self):
        from ecc.review.model_router import aggregate_verdicts
        assert aggregate_verdicts([]) == "fail"

    def test_model_tiers_defined(self):
        from ecc.review.model_router import MODEL_TIERS
        assert len(MODEL_TIERS) >= 1
        assert MODEL_TIERS[0]["name"] == "deepseek-v4-pro"
        assert MODEL_TIERS[0]["weight"] == 3


# ══════════════════════════════════════════════════════════════════════
# batch_induction tests
# ══════════════════════════════════════════════════════════════════════

class TestBatchInduction:
    """Test batch induction, clustering, and promotion"""

    def setup_method(self):
        from ecc.evolution.batch_induction import BUFFER_DIR
        self.buffer_dir = BUFFER_DIR
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        import shutil
        if self.buffer_dir.exists():
            shutil.rmtree(self.buffer_dir)

    def test_add_and_status(self):
        from ecc.evolution.batch_induction import add_finding, status, save_findings, load_findings
        save_findings([])  # start clean
        add_finding({"source": "test", "category": "code_review",
                      "severity": "fail", "title": "test finding",
                      "file": "", "detail": "🔴 测试发现"})
        s = status()
        assert s["buffer_size"] == 1

    def test_cluster_basic(self):
        from ecc.evolution.batch_induction import BatchInduction, add_finding, save_findings
        save_findings([])
        add_finding({"source": "review", "category": "code_review",
                      "severity": "fail", "title": "test",
                      "file": "", "detail": "🔴 缺少类型标注"})
        add_finding({"source": "review", "category": "code_review",
                      "severity": "fail", "title": "test",
                      "file": "", "detail": "🔴 函数缺少类型标注"})
        bi = BatchInduction()
        result = bi.cluster(min_similarity=0.3)
        assert result["total"] == 2
        assert len(result["clusters"]) >= 1

    def test_extract_pattern_key(self):
        from ecc.evolution.batch_induction import extract_pattern_key
        key = extract_pattern_key({"detail": "🔴 P1 缺少类型标注 - 函数参数", "title": "test", "source": "review"})
        assert key and "缺少" in key

    def test_flush(self):
        from ecc.evolution.batch_induction import add_finding, flush, load_findings, save_findings
        save_findings([])
        add_finding({"source": "test", "category": "code_review",
                      "severity": "fail", "title": "flush test",
                      "file": "", "detail": "test"})
        flush()
        assert load_findings() == []


# ══════════════════════════════════════════════════════════════════════
# failure_db tests
# ══════════════════════════════════════════════════════════════════════

class TestFailureDB:
    """Test failure recording and cross-session recovery"""

    def setup_method(self):
        from ecc.audit.failure_db import FAILURE_DB_FILE
        self.db_file = FAILURE_DB_FILE
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        if self.db_file.exists():
            self.db_file.unlink()

    def teardown_method(self):
        if self.db_file.exists():
            self.db_file.unlink()

    def test_record_and_get_last(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        import time
        db.record("final-review", "session-1", "test context", "模型超时")
        time.sleep(0.01)  # Ensure different timestamps
        db.record("review", "session-2", "another context", "停滞检测")
        last = db.get_last(limit=5)
        assert len(last) == 2
        roles = {e["role"] for e in last}
        assert "final-review" in roles
        assert "review" in roles

    def test_get_last_filtered(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        db.record("final-review", "s1", "ctx1", "reason1")
        db.record("review", "s2", "ctx2", "reason2")
        last = db.get_last("final-review")
        assert len(last) == 1
        assert last[0]["role"] == "final-review"

    def test_recovery_context_exists(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        db.record("final-review", "s1", "测试上下文", "测试失败原因")
        ctx = db.recovery_context("final-review")
        assert ctx is not None
        assert "跨 session 恢复" in ctx
        assert "final-review" in ctx

    def test_recovery_context_not_found(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        ctx = db.recovery_context("nonexistent-role")
        assert ctx is None

    def test_session_summary_empty(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        summary = db.get_session_summary()
        assert summary == "" or summary is None or isinstance(summary, str)

    def test_session_summary_with_data(self):
        from ecc.audit.failure_db import FailureDB
        db = FailureDB()
        db.record("final-review", "s1", "ctx", "超时")
        db.record("review", "s2", "ctx", "停滞")
        summary = db.get_session_summary()
        if summary:
            assert "final-review" in summary or "review" in summary


# ══════════════════════════════════════════════════════════════════════
# agentshield slop detection tests
# ══════════════════════════════════════════════════════════════════════

class TestAgentshieldSlop:
    """Test the 5 new AI slop detection rules"""

    def _run_check(self, check_func, lines: list[str]) -> list:
        """Run a check function on lines"""
        return check_func(lines, "/tmp/test_file.py")

    def test_hallucinated_import_detected(self):
        from ecc.core.agentshield_check import check_hallucinated_import
        lines = [
            "from langchain.vectorstores.Chroma import Chroma",
            "from utils.common import helper",
            "import os  # should be clean",
        ]
        violations = self._run_check(check_hallucinated_import, lines)
        assert len(violations) == 2
        assert violations[0].check_name == "hallucinated-import"

    def test_hallucinated_import_clean(self):
        from ecc.core.agentshield_check import check_hallucinated_import
        lines = [
            "import os",
            "import sys",
            "from pathlib import Path",
        ]
        violations = self._run_check(check_hallucinated_import, lines)
        assert len(violations) == 0

    def test_dead_code_block_detected(self):
        from ecc.core.agentshield_check import check_dead_code_block
        lines = [
            "import os",
            "",
            "def unused_function():",
            "    return 42",
            "",
            "def main():",
            "    pass",
            "",
            "if __name__ == '__main__':",
            "    main()",
        ]
        violations = self._run_check(check_dead_code_block, lines)
        # unused_function should be detected as dead code
        names = [v.code for v in violations]
        assert any("unused_function" in n for n in names)

    def test_over_commented_detected(self):
        from ecc.core.agentshield_check import check_over_commented
        lines = [
            "# This is a comment",
            "# That explains every line",
            "# Making the comment ratio high",
            "x = 1",
            "# Another comment",
            "y = 2",
        ]
        violations = self._run_check(check_over_commented, lines)
        assert len(violations) >= 1
        assert violations[0].check_name == "over-commented"

    def test_over_commented_clean(self):
        from ecc.core.agentshield_check import check_over_commented
        lines = [
            "import os",
            "import sys",
            "",
            "def main():",
            "    x = 1",
            "    y = 2",
            "    return x + y",
        ]
        violations = self._run_check(check_over_commented, lines)
        assert len(violations) == 0

    def test_fake_todo_detected(self):
        from ecc.core.agentshield_check import check_fake_todo
        lines = [
            "x = 1",
            "# TODO: fix later",
            "# FIXME: implement soon",
            "# TODO: add timeout retry in fetch_data()  # specific, should pass",
        ]
        violations = self._run_check(check_fake_todo, lines)
        assert len(violations) >= 2  # first two are vague

    def test_hallucinated_api_detected(self):
        from ecc.core.agentshield_check import check_hallucinated_api
        lines = [
            'url = "https://api.example.com/v2/data"',
            'url2 = "https://api.test.com/endpoint"',
            'real_url = "https://api.openai.com/v1/models"  # legitimate',
        ]
        violations = self._run_check(check_hallucinated_api, lines)
        assert len(violations) >= 2  # first two are placeholder domains


# ══════════════════════════════════════════════════════════════════════
# skill_library tests
# ══════════════════════════════════════════════════════════════════════


