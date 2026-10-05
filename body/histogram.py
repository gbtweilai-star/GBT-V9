# body/histogram.py —— 分位数：双后端共用同一套桶与算法，禁止各自算
BUCKETS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10_000,
              30_000, 60_000, 300_000, 1_800_000)
ALGO = "fixed-histogram-v1"


def bucket_index(ms: float) -> int:
    for i, ub in enumerate(BUCKETS_MS):
        if ms <= ub:
            return i
    return len(BUCKETS_MS) - 1


def add(counts: dict, ms: float) -> None:
    k = str(bucket_index(ms))
    counts[k] = counts.get(k, 0) + 1


def merge(*histograms: dict) -> dict:
    out: dict[str, int] = {}
    for h in histograms:
        for k, v in (h or {}).items():
            out[str(k)] = out.get(str(k), 0) + int(v)
    return out


def quantile(histogram: dict, q: float) -> float | None:
    """nearest-rank：累计计数达到 ceil(q×total) 的那个桶的上界。"""
    total = sum(int(v) for v in histogram.values())
    if total == 0:
        return None
    rank = max(1, int(-(-q * total // 1)))          # ceil
    cum = 0
    for i, ub in enumerate(BUCKETS_MS):
        cum += int(histogram.get(str(i), 0))
        if cum >= rank:
            return float(ub)
    return float(BUCKETS_MS[-1])
