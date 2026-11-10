"""Análise do sistema A: latência de visibilidade, escritas ao destino e correcção final.

Definição de "visível": um evento com changed_at = t fica visível no primeiro REFRESH com read_at >= t.
A latência é written_at desse refresh - t. Heartbeats não contam (dizem "actualizado" sem reler os dados;
é exactamente isso que falha nos casos em que o sentinela não muda).
"""
from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def load_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank."""
    if not sorted_vals:
        return float("nan")
    k = max(1, math.ceil(p * len(sorted_vals) - 1e-9))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def visibility_latencies(events: list[dict], writes: list[dict]) -> dict[str, list[float | None]]:
    refreshes = sorted((w for w in writes if w["type"] == "refresh"), key=lambda w: w["read_at"])
    read_ats = [_dt(w["read_at"]) for w in refreshes]
    out: dict[str, list[float | None]] = defaultdict(list)
    for e in events:
        t = _dt(e["changed_at"])
        i = bisect_left(read_ats, t)
        if i >= len(refreshes):
            out[e["scenario"]].append(None)
        else:
            out[e["scenario"]].append((_dt(refreshes[i]["written_at"]) - t).total_seconds())
    return out


def final_mismatches(writes: list[dict], truth: list[list]) -> list[dict]:
    refreshes = [w for w in writes if w["type"] == "refresh"]
    got = {}
    if refreshes:
        last = max(refreshes, key=lambda w: w["written_at"])
        got = {(v, d): (Decimal(t), n) for v, d, t, n in last["aggregates"]}
    want = {(v, d): (Decimal(t), n) for v, d, t, n in truth}
    bad = []
    for k in sorted(set(got) | set(want)):
        if got.get(k) != want.get(k):
            bad.append({"vendedor": k[0], "dia": k[1],
                        "destino": None if k not in got else [str(got[k][0]), got[k][1]],
                        "origem": None if k not in want else [str(want[k][0]), want[k][1]]})
    return bad


def summarize(events, writes, truth=None) -> dict:
    lat = visibility_latencies(events, writes)
    per = {}
    for name, vals in lat.items():
        seen = sorted(v for v in vals if v is not None)
        per[name] = {"eventos": len(vals), "nunca_visiveis": len(vals) - len(seen),
                     "p50_s": percentile(seen, 0.50), "p95_s": percentile(seen, 0.95),
                     "p99_s": percentile(seen, 0.99), "max_s": seen[-1] if seen else float("nan")}
    kinds = defaultdict(int)
    docs_written = 0
    for w in writes:
        if w["type"] in ("refresh", "heartbeat"):
            kinds[w["type"]] += 1
            docs_written += w["docs_written"]
    stats = next((w for w in reversed(writes) if w["type"] == "stats"), {})
    res = {"latencia_por_cenario": per, "refreshes": kinds["refresh"], "heartbeats": kinds["heartbeat"],
           "escritas_ao_destino": docs_written, "queries_a_origem": stats.get("queries")}
    if truth is not None:
        bad = final_mismatches(writes, truth)
        res["estado_final"] = {"linhas_com_diferenca": len(bad), "exemplos": bad[:5]}
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cdcstudy.analyze")
    p.add_argument("--events", default="runs/events.jsonl")
    p.add_argument("--writes", default="runs/a.jsonl")
    p.add_argument("--truth", default=None, help="runs/truth.json (opcional)")
    a = p.parse_args(argv)
    truth = json.loads(Path(a.truth).read_text()) if a.truth else None
    print(json.dumps(summarize(load_jsonl(a.events), load_jsonl(a.writes), truth),
                     indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
