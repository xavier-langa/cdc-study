"""Sistema A: polling com valor-sentinela (reconstrução genérica da lógica do Daily Sales).

A cada `interval` segundos:
  1. lê o sentinela (total de vendas do mês);
  2. se mudou face ao anterior, lê os agregados por vendedor/dia e escreve no destino (refresh);
  3. se não mudou, só escreve um carimbo de "actualizado às ..." (heartbeat).

Suposição a confirmar: no Daily Sales original, o refresh reescrevia todos os documentos
(write_mode=all). Se só reescrevia os que mudaram, usa --write-mode changed.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable


class JsonlSink:
    """Regista cada escrita ao destino em JSONL (é daqui que a análise tira latências e custos)."""

    def __init__(self, path: str | Path, write_mode: str = "all"):
        assert write_mode in ("all", "changed")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")
        self.write_mode = write_mode
        self._last: dict[str, tuple[str, int]] = {}

    @staticmethod
    def _key(v: int, d: date) -> str:
        return f"{v}_{d.isoformat()}"

    def _diff(self, aggs):
        new = {self._key(v, d): (str(t), n) for v, d, t, n in aggs}
        if self.write_mode == "all":
            to_write = dict(new)
        else:
            to_write = {k: val for k, val in new.items() if self._last.get(k) != val}
        to_delete = [k for k in self._last if k not in new]
        self._last = new
        return to_write, to_delete

    def _apply(self, to_write: dict, to_delete: list) -> None:
        """Gancho para destinos reais (ver PostgresSink)."""

    def _emit(self, rec: dict) -> None:
        self._f.write(json.dumps(rec) + "\n")
        self._f.flush()

    def refresh(self, aggs, read_at: datetime, written_at: datetime) -> None:
        to_write, to_delete = self._diff(aggs)
        self._apply(to_write, to_delete)
        self._emit({"type": "refresh", "read_at": read_at.isoformat(), "written_at": written_at.isoformat(),
                    "docs_written": len(to_write) + len(to_delete),
                    "aggregates": [[v, d.isoformat(), str(t), n] for v, d, t, n in aggs]})

    def heartbeat(self, written_at: datetime) -> None:
        self._emit({"type": "heartbeat", "written_at": written_at.isoformat(), "docs_written": 1})

    def stats(self, stats: dict) -> None:
        self._emit({"type": "stats", **stats})

    def close(self) -> None:
        self._f.close()


class PostgresSink(JsonlSink):
    """Escreve também num Postgres (destino do painel). Variáveis: PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB."""

    DDL = (
        "CREATE TABLE IF NOT EXISTS vendas_dia (chave text PRIMARY KEY, total numeric NOT NULL, n_docs integer NOT NULL)",
        "CREATE TABLE IF NOT EXISTS meta (id integer PRIMARY KEY, updated_at timestamptz NOT NULL)",
    )

    def __init__(self, path, write_mode="all"):
        super().__init__(path, write_mode)
        import os

        import psycopg

        self.conn = psycopg.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", "Str0ng!Passw0rd"),
            dbname=os.environ.get("PG_DB", "destino"),
            autocommit=True,
        )
        with self.conn.cursor() as cur:
            for ddl in self.DDL:
                cur.execute(ddl)

    def _apply(self, to_write, to_delete):
        with self.conn.transaction(), self.conn.cursor() as cur:
            for k, (total, n) in to_write.items():
                cur.execute(
                    "INSERT INTO vendas_dia (chave, total, n_docs) VALUES (%s, %s, %s) "
                    "ON CONFLICT (chave) DO UPDATE SET total = EXCLUDED.total, n_docs = EXCLUDED.n_docs",
                    (k, total, n),
                )
            for k in to_delete:
                cur.execute("DELETE FROM vendas_dia WHERE chave = %s", (k,))

    def heartbeat(self, written_at):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta (id, updated_at) VALUES (1, %s) "
                "ON CONFLICT (id) DO UPDATE SET updated_at = EXCLUDED.updated_at",
                (written_at,),
            )
        super().heartbeat(written_at)

    def close(self) -> None:
        self.conn.close()
        super().close()


class PollerA:
    def __init__(self, source, sink):
        self.source, self.sink = source, sink
        self.last_sentinel: Decimal | None = None  # no original estava numa variável de ambiente
        self.stats = {"ticks": 0, "refreshes": 0, "heartbeats": 0, "queries": 0}

    def tick(self) -> str:
        src = self.source
        self.stats["ticks"] += 1
        now = src.now_utc()
        month_start = date(now.year, now.month, 1)
        s = src.sentinel(month_start)
        self.stats["queries"] += 1
        if s != self.last_sentinel:
            read_at = src.now_utc()  # tudo o que foi confirmado antes de read_at está nos agregados
            aggs = src.aggregates(month_start)
            self.stats["queries"] += 1
            self.sink.refresh(aggs, read_at, src.now_utc())
            self.last_sentinel = s
            self.stats["refreshes"] += 1
            return "refresh"
        self.sink.heartbeat(src.now_utc())
        self.stats["heartbeats"] += 1
        return "heartbeat"

    def run(self, interval_s: float, duration_s: float,
            monotonic: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep) -> None:
        t0 = monotonic()
        nxt = t0
        while monotonic() - t0 < duration_s:
            self.tick()
            nxt += interval_s
            sleep(max(0.0, nxt - monotonic()))
        self.sink.stats(self.stats)


def main(argv=None) -> int:
    from .source import MssqlSource

    p = argparse.ArgumentParser(prog="cdcstudy.system_a")
    p.add_argument("--interval", type=float, default=60)
    p.add_argument("--duration-min", type=float, default=60)
    p.add_argument("--sink", choices=["jsonl", "postgres"], default="jsonl")
    p.add_argument("--write-mode", choices=["all", "changed"], default="all")
    p.add_argument("--out", default="runs/a.jsonl")
    a = p.parse_args(argv)

    sink = (PostgresSink if a.sink == "postgres" else JsonlSink)(a.out, a.write_mode)
    poller = PollerA(MssqlSource(), sink)
    try:
        poller.run(a.interval, a.duration_min * 60)
    except KeyboardInterrupt:
        sink.stats(poller.stats)
    finally:
        sink.close()
    print(poller.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
