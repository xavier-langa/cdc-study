"""Gerador de carga: escreve na tabela Documentos e regista cada alteração (events.jsonl).

Subcomandos:
  continuous  tráfego contínuo (chegadas de Poisson, perfil horário)
  scenario    cenários escritos à mão (ver scenarios.py)
  truth       grava os agregados da origem num ficheiro (verdade para a análise final)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .source import Change, MssqlSource

TZ_OFFSET_H = 2  # Maputo (UTC+2); só serve para escolher a hora do perfil e a data do documento

# PLACEHOLDER: forma do dia de vendas (peso relativo por hora local, 0-23).
# Substitui pela forma que conheces do Oceana (apenas a forma agregada, sem dados da empresa).
HOURLY_WEIGHTS = [0.1, 0.05, 0.05, 0.05, 0.1, 0.3, 0.8, 1.5, 2.2, 2.4, 2.3, 2.0,
                  1.6, 1.8, 2.2, 2.3, 2.0, 1.4, 0.7, 0.4, 0.25, 0.2, 0.15, 0.1]


def rate_per_second(docs_per_month: float, local_hour: int, speedup: float = 1.0) -> float:
    """Taxa média de eventos/s para a hora dada. A média ao longo das 24 h é docs_per_month/(30 dias)."""
    mean = docs_per_month / (30 * 86400)
    w = HOURLY_WEIGHTS[local_hour] / (sum(HOURLY_WEIGHTS) / 24)
    return mean * w * speedup


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")
        self._n = 0

    def log(self, scenario: str, ch: Change, **extra) -> None:
        self._n += 1
        rec = {"scenario": scenario, "op": ch.op, "doc_id": ch.doc_id, "version": ch.version,
               "changed_at": ch.changed_at.isoformat(), **extra}
        self._f.write(json.dumps(rec) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def local_today(source) -> date:
    return (source.now_utc() + timedelta(hours=TZ_OFFSET_H)).date()


def run_continuous(source, log: EventLog, *, docs_per_month: float, duration_s: float,
                   speedup: float = 1.0, rng: random.Random | None = None,
                   monotonic: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep,
                   vendedores: int = 24, clientes: int = 200,
                   p_update: float = 0.12, p_delete: float = 0.01, p_nc: float = 0.08) -> int:
    rng = rng or random.Random()
    live: list[int] = []
    t_end = monotonic() + duration_s
    n = 0
    while monotonic() < t_end:
        hour = ((source.now_utc() + timedelta(hours=TZ_OFFSET_H)).hour)
        rate = rate_per_second(docs_per_month, hour, speedup)
        sleep(rng.expovariate(rate))
        if monotonic() >= t_end:
            break
        r = rng.random()
        if live and r < p_delete:
            doc = live.pop(rng.randrange(len(live)))
            ch = source.delete_doc(doc)
        elif live and r < p_delete + p_update:
            doc = rng.choice(live)
            ch = source.update_total(doc, Decimal(f"{rng.uniform(50, 5000):.2f}"))
        else:
            tipo = "NC" if rng.random() < p_nc else "FT"
            total = Decimal(f"{rng.lognormvariate(6.5, 0.8):.2f}")
            ch = source.insert_doc(tipo, local_today(source), rng.randint(1, vendedores),
                                   rng.randint(1, clientes), total)
            live.append(ch.doc_id)
            live = live[-5000:]
        log.log("continuous", ch)
        n += 1
    return n


def _source_from_env() -> MssqlSource:
    return MssqlSource()


def main(argv: list[str] | None = None) -> int:
    from . import scenarios

    p = argparse.ArgumentParser(prog="cdcstudy.generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("continuous")
    c.add_argument("--docs-per-month", type=float, default=30000)
    c.add_argument("--duration-min", type=float, default=60)
    c.add_argument("--speedup", type=float, default=1.0, help="multiplica a taxa (teste de carga)")
    c.add_argument("--events", default="runs/events.jsonl")
    c.add_argument("--seed", type=int)

    s = sub.add_parser("scenario")
    s.add_argument("name", choices=sorted(scenarios.SCENARIOS))
    s.add_argument("--repeat", type=int, default=20)
    s.add_argument("--events", default="runs/events.jsonl")
    s.add_argument("--seed", type=int)

    t = sub.add_parser("truth")
    t.add_argument("--out", default="runs/truth.json")

    a = p.parse_args(argv)
    src = _source_from_env()

    if a.cmd == "truth":
        since = date(local_today(src).year, local_today(src).month, 1)
        rows = [[v, d.isoformat(), str(tot), n] for v, d, tot, n in src.aggregates(since)]
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rows))
        print(f"{len(rows)} linhas (vendedor, dia) gravadas em {a.out}")
        return 0

    log = EventLog(a.events)
    rng = random.Random(a.seed)
    try:
        if a.cmd == "continuous":
            n = run_continuous(src, log, docs_per_month=a.docs_per_month,
                               duration_s=a.duration_min * 60, speedup=a.speedup, rng=rng)
        else:
            n = scenarios.SCENARIOS[a.name](src, log, rng, repeat=a.repeat)
        print(f"{n} alterações escritas; registo em {a.events}")
    except KeyboardInterrupt:
        print("interrompido", file=sys.stderr)
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
