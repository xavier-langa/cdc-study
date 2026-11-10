"""Testa o PostgresSink contra um Postgres real. É ignorado se não houver Postgres acessível
(variáveis PG_HOST, PG_PORT, PG_USER, PG_PASSWORD, PG_DB)."""
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")
from cdcstudy.system_a import PostgresSink


def _reachable() -> bool:
    try:
        psycopg.connect(
            host=os.environ.get("PG_HOST", "localhost"), port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("PG_USER", "postgres"), password=os.environ.get("PG_PASSWORD", "Str0ng!Passw0rd"),
            dbname=os.environ.get("PG_DB", "destino"), connect_timeout=2,
        ).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="sem Postgres acessível")


def test_upsert_delete_and_heartbeat(tmp_path):
    sink = PostgresSink(tmp_path / "a.jsonl", "all")
    with sink.conn.cursor() as cur:
        cur.execute("TRUNCATE vendas_dia")
    now = datetime.now(timezone.utc)
    sink.refresh([(1, date(2026, 11, 1), Decimal("100.50"), 3), (2, date(2026, 11, 1), Decimal("20"), 1)], now, now)
    sink.refresh([(1, date(2026, 11, 1), Decimal("130.50"), 4)], now, now)  # vendedor 2 desaparece
    sink.heartbeat(now)
    with sink.conn.cursor() as cur:
        cur.execute("SELECT chave, total, n_docs FROM vendas_dia ORDER BY chave")
        assert cur.fetchall() == [("1_2026-11-01", Decimal("130.50"), 4)]
        cur.execute("SELECT count(*) FROM meta")
        assert cur.fetchone()[0] == 1
    sink.close()
