#!/usr/bin/env python3
"""
batch_induction.py — 批量归纳引擎

从 tri_role 审查中收集发现，暂存到缓冲区。当缓冲区的发现达到阈值后，
自动聚类 → 频次≥2 的模式提升到 coding-patterns.yaml → 丢弃单次噪音。

设计原理（2026-06-17）：
避免「一次经验」泛化为「长期规则」。只有重复出现的模式才值得记住。

用法：
    python3 batch_induction.py collect  # 聚合缓冲区 → 提升 → 清理
    python3 batch_induction.py status   # 查看缓冲区状态
    python3 batch_induction.py flush    # 强制清空缓冲区（不提升）
"""

import json, sys, os, re
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

from ecc import config
BASE = config._ECC_HOME
BUFFER_DIR = config.BUFFER_DIR
FINDINGS_FILE = BUFFER_DIR / "findings.json"
NOISE_LOG = BUFFER_DIR / "noise_log.jsonl"
CODING_PATTERNS_YAML = config.CODING_PATTERNS_FILE

# 提升阈值：相同模式出现 N 次后才升级到 coding-patterns.yaml
# 系统越成熟阈值越高（由 system_maturity 控制）
DEFAULT_PROMOTE_THRESHOLD = 2

# 缓冲区最大条数
MAX_BUFFER_SIZE = 100


def ensure_dirs():
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)


def load_findings() -> list[dict]:
    """从缓冲区 JSON 加载发现"""
    if not FINDINGS_FILE.exists():
        return []
    try:
        data = json.loads(FINDINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_findings(findings: list[dict]):
    """原子写入缓冲区"""
    ensure_dirs()
    tmp = FINDINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(findings, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    tmp.replace(FINDINGS_FILE)


def log_noise(finding: dict):
    """将噪音写入噪音日志"""
    ensure_dirs()
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finding": finding,
        "reason": "frequency_below_threshold",
    }
    with open(NOISE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_pattern_key(finding: dict) -> str:
    """从发现中提取模式键（用于聚类去重）"""
    detail = finding.get("detail", "")
    title = finding.get("title", "")
    source = finding.get("source", "")
    # 提取关键特征短语
    m = re.search(r'[🔴🟡]\s*(P\d)?\s*[:：]?\s*(.+?)(?:\s*[-–—]|$)', detail)
    if m:
        return m.group(2).strip()[:40]
    # 回退到 title 的短摘要
    return title[:40]


def cluster_findings(findings: list[dict]) -> dict[str, list[dict]]:
    """按模式键聚类发现"""
    clusters = defaultdict(list)
    for f in findings:
        key = extract_pattern_key(f)
        clusters[key].append(f)
    return dict(clusters)


def promote_to_coding_pattern(pattern_key: str, findings: list[dict],
                               threshold: int = None) -> bool:
    """
    将聚类的发现提升到 coding-patterns.yaml
    threshold: 提升所需的最低频次
    """
    if threshold is None:
        # 从 system_maturity 获取动态阈值
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from system_maturity import SystemMaturity
            threshold = SystemMaturity.promote_threshold()
        except Exception:
            threshold = DEFAULT_PROMOTE_THRESHOLD

    count = len(findings)
    if count < threshold:
        return False

    # 提取描述 + 严重度
    severities = [f.get("severity", "info") for f in findings]
    sources = list(set(f.get("source", "") for f in findings))
    sample_detail = findings[0].get("detail", "")[:200]

    # 估算置信度 = 频率 / 阈值, 上限 0.9
    confidence = min(count / threshold, 0.9)

    # 写入 coding-patterns.yaml
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from coding_scout import record_coding_pattern, load_coding_patterns

        pattern_name = "review-" + pattern_key.split()[0].lower().strip(",.()[]:;")[:20]

        record_coding_pattern(
            pattern=pattern_name,
            description=pattern_key[:80],
            benefit=f"batch_induction 提升 (出现{count}次, 来自: {', '.join(sources)})",
            trigger=sample_detail,
            fix_effort_hours=0.5,
            confidence=confidence,
            frequency="always" if confidence >= 0.7 else "sometimes",
        )
        return True
    except Exception as e:
        print(f"batch_induction: 提升失败 ({e})", file=sys.stderr)
        return False


def collect(force: bool = False):
    """聚合发现：聚类 → 提升 → 清理缓冲区"""
    findings = load_findings()
    if not findings:
        print("batch_induction: 缓冲区为空，无需聚合")
        return

    print(f"batch_induction: 加载 {len(findings)} 条发现")

    # 获取动态提升阈值
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from system_maturity import SystemMaturity
        threshold = SystemMaturity.promote_threshold()
    except Exception:
        threshold = DEFAULT_PROMOTE_THRESHOLD

    if force:
        threshold = 1  # force 模式降低门槛

    print(f"batch_induction: 提升阈值 = {threshold}")

    # 聚类
    clusters = cluster_findings(findings)
    print(f"batch_induction: {len(clusters)} 个聚类")

    promoted = 0
    noise = 0
    retained_findings = []

    for key, group in clusters.items():
        if len(group) >= threshold:
            if promote_to_coding_pattern(key, group, threshold=threshold):
                promoted += 1
                print(f"  ✅ 提升: \"{key}\" ({len(group)}次)")
        else:
            noise += len(group)
            for f in group:
                log_noise(f)
            print(f"  📝 噪音: \"{key}\" ({len(group)}次 < 阈值{threshold})")

    print(f"\nbatch_induction: 提升 {promoted} 条, 噪音 {noise} 条")

    # 清空缓冲区（提升过的已记录到 coding-patterns，噪音已归档）
    save_findings([])

    # 记录到健康报告
    try:
        report = BASE / "tmp" / "ecc_evolve_report.md"
        if report.exists():
            content = report.read_text(encoding="utf-8")
            entry = f"\n### batch_induction ({datetime.now().strftime('%H:%M')})\n- 加载 {len(findings)} 条, 聚类 {len(clusters)} 个\n- 提升 {promoted} 条, 噪音 {noise} 条\n"
            report.write_text(content + entry, encoding="utf-8")
    except Exception:
        pass


def status() -> dict:
    """查看缓冲区状态"""
    findings = load_findings()
    clusters = cluster_findings(findings)
    return {
        "buffer_size": len(findings),
        "clusters": len(clusters),
        "findings_by_source": dict(Counter(f.get("source", "unknown") for f in findings)),
        "clusters_detail": {k: len(v) for k, v in sorted(clusters.items(), key=lambda x: -len(x[1]))},
    }


def add_finding(finding: dict):
    """添加一条发现到缓冲区（供 tri_role.py 调用）"""
    findings = load_findings()
    findings.append(finding)

    # 限制缓冲区大小（FIFO）
    if len(findings) > MAX_BUFFER_SIZE:
        findings = findings[-MAX_BUFFER_SIZE:]

    save_findings(findings)

    # 如果缓冲区达到阈值大小，自动聚合
    if len(findings) >= 10:
        collect()


def flush():
    """强制清空缓冲区（不提升）"""
    findings = load_findings()
    for f in findings:
        log_noise(f)
    save_findings([])
    print(f"batch_induction: 已清空 {len(findings)} 条（全部归档到噪音日志）")


class BatchInduction:
    """与 tri_role.py 兼容的接口类"""

    def add_finding(self, finding: dict):
        """添加一条发现（与 tri_role.py 的调用签名匹配）"""
        add_finding(finding)

    def collect(self, force: bool = False):
        """聚合提升"""
        collect(force=force)

    def status(self) -> dict:
        """缓冲区状态"""
        return status()

    def flush(self):
        """清空缓冲区"""
        flush()

    def cluster(self, min_similarity: float = 0.3) -> dict:
        """使用 TF 向量 + 余弦相似度的密度聚类（无需外部依赖）"""
        findings = load_findings()
        if len(findings) < 2:
            return {"clusters": [], "noise": len(findings), "method": "insufficient_data"}

        # 1. 构建词频向量
        import math
        from collections import Counter

        def tokenize(text: str) -> list[str]:
            words = re.findall(r'\b[a-zA-Z\u4e00-\u9fff]{2,}\b', text.lower())
            return words

        # 提取每个 finding 的词袋
        docs = []
        for f in findings:
            text = f.get("detail", "") + " " + f.get("title", "") + " " + f.get("source", "")
            docs.append(tokenize(text))

        # 构建词频向量（TF）
        all_terms = set()
        for doc in docs:
            all_terms.update(doc)
        term_list = sorted(all_terms)
        term_index = {t: i for i, t in enumerate(term_list)}

        # 构建词频向量（TF with log normalization）
        vectors = []
        for doc in docs:
            vec = [0.0] * len(term_list)
            tf = Counter(doc)
            for word, count in tf.items():
                if word in term_index:
                    vec[term_index[word]] = 1.0 + math.log(count)  # TF with log normalization
            vectors.append(vec)

        # 2. 余弦相似度密度聚类
        def cosine_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        n = len(vectors)
        assigned = [None] * n
        next_cluster = 0

        for i in range(n):
            if assigned[i] is not None:
                continue
            # Start a new cluster
            cluster_members = [i]
            assigned[i] = next_cluster
            for j in range(i + 1, n):
                if assigned[j] is None:
                    sim = cosine_sim(vectors[i], vectors[j])
                    if sim >= min_similarity:
                        cluster_members.append(j)
                        assigned[j] = next_cluster
            next_cluster += 1

        # 3. 构建结果
        clusters = {}
        noise_indices = []
        for i, cid in enumerate(assigned):
            if cid is None:
                noise_indices.append(i)
                continue
            if cid not in clusters:
                clusters[cid] = {"members": [], "key": extract_pattern_key(findings[i])}
            clusters[cid]["members"].append(findings[i])

        return {
            "clusters": list(clusters.values()),
            "noise": len(noise_indices),
            "noise_indices": noise_indices,
            "method": "tf-cosine",
            "total": n,
        }


def main():
    if len(sys.argv) < 2:
        print("用法: python3 batch_induction.py <collect|status|flush|add>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "collect":
        force = "--force" in sys.argv
        collect(force=force)
    elif cmd == "status":
        s = status()
        print(f"缓冲区大小: {s['buffer_size']}")
        print(f"聚类数: {s['clusters']}")
        print(f"来源分布: {json.dumps(s['findings_by_source'], ensure_ascii=False)}")
        print(f"聚类详情:")
        for k, v in s["clusters_detail"].items():
            marker = " ⚠️" if v >= 2 else ""
            print(f"  [{v}] {k}{marker}")
        sys.exit(0 if s["buffer_size"] < 10 else 1)
    elif cmd == "flush":
        flush()
    elif cmd == "add":
        finding = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        add_finding(finding)
        print(f"已添加: {finding.get('title', '?')}")
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
