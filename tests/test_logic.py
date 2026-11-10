"""Testes da lógica com origem simulada (FakeSource). Não exercitam T-SQL nem o SQL Server."""
import json
import random
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from cdcstudy import analyze, scenarios
from cdcstudy.generator import EventLog, HOURLY_WEIGHTS, local_today, rate_per_second, run_continuous
from cdcstudy.source import FakeSource
from cdcstudy.system_a import JsonlSink, PollerA


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 27, 8, 0, 0)  # 10:00 em Maputo

    def now(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    src = FakeSource(clock.now)
    sink = JsonlSink(tmp_path / "a.jsonl")
    log = EventLog(tmp_path / "events.jsonl")
    return clock, src, sink, log, PollerA(src, sink), tmp_path


def _results(env, with_truth=True):
    clock, src, sink, log, poller, tmp = env
    sink.stats(poller.stats)
    sink.close()
    log.close()
    events = analyze.load_jsonl(tmp / "events.jsonl")
    writes = analyze.load_jsonl(tmp / "a.jsonl")
    truth = None
    if with_truth:
        truth = [[v, d.isoformat(), str(t), n] for v, d, t, n in src.aggregates(datetime(2026, 10, 1).date())]
    return analyze.summarize(events, writes, truth)


def test_rate_mean_matches_monthly_volume():
    mean = sum(rate_per_second(30000, h) for h in range(24)) / 24
    assert mean == pytest.approx(30000 / (30 * 86400))
    assert len(HOURLY_WEIGHTS) == 24


def test_normal_insert_is_seen_with_expected_latency(env):
    clock, src, sink, log, poller, _ = env
    assert poller.tick() == "refresh"        # primeira passagem
    clock.advance(10)
    log.log("t", src.insert_doc("FT", local_today(src), 1, 1, Decimal("100")))
    clock.advance(20)
    assert poller.tick() == "refresh"
    r = _results(env)
    assert r["latencia_por_cenario"]["t"]["p50_s"] == pytest.approx(20)
    assert r["estado_final"]["linhas_com_diferenca"] == 0


def test_compensating_pair_is_missed_by_sentinel(env):
    clock, src, sink, log, poller, _ = env
    poller.tick()
    day = local_today(src)
    clock.advance(5)
    log.log("pair", src.insert_doc("FT", day, 3, 1, Decimal("250")))
    clock.advance(2)
    log.log("pair", src.insert_doc("NC", day, 3, 1, Decimal("250")))
    clock.advance(30)
    assert poller.tick() == "heartbeat"      # sentinela igual -> não relê
    r = _results(env)
    assert r["latencia_por_cenario"]["pair"]["nunca_visiveis"] == 2
    bad = r["estado_final"]
    ex = bad["exemplos"][0]
    assert bad["linhas_com_diferenca"] == 1
    assert Decimal(ex["origem"][0]) == 0 and ex["origem"][1] == 2      # origem: 2 docs, total 0
    assert ex["destino"] is None                                       # destino nem conhece o vendedor/dia


def test_reassign_vendedor_is_missed_by_sentinel(env):
    clock, src, sink, log, poller, _ = env
    ch = src.insert_doc("FT", local_today(src), 1, 1, Decimal("500"))
    log.log("re", ch)
    clock.advance(30)
    poller.tick()
    clock.advance(30)
    log.log("re", src.update_vendedor(ch.doc_id, 2))
    clock.advance(30)
    assert poller.tick() == "heartbeat"
    r = _results(env)
    assert r["estado_final"]["linhas_com_diferenca"] == 2   # vendedor 1 e vendedor 2 errados


def test_delete_changes_sentinel_and_is_seen(env):
    clock, src, sink, log, poller, _ = env
    ch = src.insert_doc("FT", local_today(src), 1, 1, Decimal("100"))
    log.log("d", ch)
    clock.advance(30)
    poller.tick()
    clock.advance(10)
    log.log("d", src.delete_doc(ch.doc_id))
    clock.advance(20)
    assert poller.tick() == "refresh"
    r = _results(env)
    assert r["latencia_por_cenario"]["d"]["nunca_visiveis"] == 0
    assert r["estado_final"]["linhas_com_diferenca"] == 0


def test_write_mode_changed_writes_fewer_docs(tmp_path):
    def run(mode):
        clock = Clock()
        src = FakeSource(clock.now)
        sink = JsonlSink(tmp_path / f"{mode}.jsonl", mode)
        poller = PollerA(src, sink)
        day = local_today(src)
        src.insert_doc("FT", day, 1, 1, Decimal("10"))
        src.insert_doc("FT", day, 2, 1, Decimal("20"))
        poller.tick()
        src.insert_doc("FT", day, 1, 1, Decimal("5"))
        poller.tick()
        sink.close()
        return [w["docs_written"] for w in analyze.load_jsonl(tmp_path / f"{mode}.jsonl")]
    assert run("all") == [2, 2]
    assert run("changed") == [2, 1]


def test_percentile_nearest_rank():
    v = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert analyze.percentile(v, 0.5) == 5.0
    assert analyze.percentile(v, 0.95) == 10.0
    assert analyze.percentile(v, 0.1) == 1.0


def test_run_continuous_logs_every_change(tmp_path):
    clock = Clock()
    t = [0.0]

    def sleep(s):
        t[0] += s
        clock.advance(s)

    src = FakeSource(clock.now)
    log = EventLog(tmp_path / "e.jsonl")
    n = run_continuous(src, log, docs_per_month=30000, duration_s=3600, speedup=60,
                       rng=random.Random(1), monotonic=lambda: t[0], sleep=sleep)
    log.close()
    lines = (tmp_path / "e.jsonl").read_text().splitlines()
    assert n == len(lines) > 100
    assert {json.loads(x)["op"] for x in lines} >= {"insert", "update"}


def test_scenarios_run_against_fake(tmp_path):
    clock = Clock()
    src = FakeSource(clock.now)
    log = EventLog(tmp_path / "e.jsonl")
    sleep = lambda s: clock.advance(s)
    n = 0
    for name, fn in scenarios.SCENARIOS.items():
        n += fn(src, log, random.Random(0), repeat=2, sleep=sleep)
    log.close()
    assert n == len((tmp_path / "e.jsonl").read_text().splitlines())
