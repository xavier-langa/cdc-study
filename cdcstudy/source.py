"""Acesso à origem (a "ERP"). Duas implementações com a mesma interface:

- MssqlSource: SQL Server real (T-SQL). NÃO foi executada contra um SQL Server
  durante o desenvolvimento; é a primeira coisa a validar quando correres o docker compose.
- FakeSource: em memória, usada nos testes da lógica.

Todos os timestamps são UTC naive, vindos do relógio do SQL Server (SYSUTCDATETIME),
para que changed_at e read_at usem o mesmo relógio.
"""
from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable


@dataclass(frozen=True)
class Change:
    op: str            # insert | update | delete
    doc_id: int
    version: int
    changed_at: datetime


def signed(tipo: str, total: Decimal) -> Decimal:
    """Notas de crédito (NC) subtraem às vendas."""
    return -total if tipo == "NC" else total


class MssqlSource:
    def __init__(self, host: str | None = None, port: int | None = None,
                 user: str = "sa", password: str | None = None, database: str = "SalesSim"):
        import pymssql  # import tardio: os testes não precisam de driver

        self.conn = pymssql.connect(
            server=host or os.environ.get("MSSQL_HOST", "localhost"),
            port=str(port or os.environ.get("MSSQL_PORT", 1433)),
            user=user,
            password=password or os.environ.get("MSSQL_SA_PASSWORD", "Str0ng!Passw0rd"),
            database=database,
            autocommit=True,  # cada instrução faz commit; changed_at ≈ instante do commit
        )

    def _exec(self, sql: str, params: tuple = ()):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def now_utc(self) -> datetime:
        return self._exec("SELECT SYSUTCDATETIME()").fetchone()[0]

    def insert_doc(self, tipo: str, data: date, vendedor: int, cliente: int, total: Decimal) -> Change:
        row = self._exec(
            "INSERT INTO dbo.Documentos (Tipo, Data, VendedorId, ClienteId, Total) "
            "OUTPUT INSERTED.DocId, INSERTED.Version, INSERTED.ChangedAt "
            "VALUES (%s, %s, %s, %s, %s)",
            (tipo, data, vendedor, cliente, total),
        ).fetchone()
        return Change("insert", int(row[0]), int(row[1]), row[2])

    def update_total(self, doc_id: int, total: Decimal) -> Change:
        row = self._exec(
            "UPDATE dbo.Documentos SET Total = %s, Version = Version + 1, ChangedAt = SYSUTCDATETIME() "
            "OUTPUT INSERTED.DocId, INSERTED.Version, INSERTED.ChangedAt WHERE DocId = %s",
            (total, doc_id),
        ).fetchone()
        return Change("update", int(row[0]), int(row[1]), row[2])

    def update_vendedor(self, doc_id: int, vendedor: int) -> Change:
        row = self._exec(
            "UPDATE dbo.Documentos SET VendedorId = %s, Version = Version + 1, ChangedAt = SYSUTCDATETIME() "
            "OUTPUT INSERTED.DocId, INSERTED.Version, INSERTED.ChangedAt WHERE DocId = %s",
            (vendedor, doc_id),
        ).fetchone()
        return Change("update", int(row[0]), int(row[1]), row[2])

    def delete_doc(self, doc_id: int) -> Change:
        row = self._exec(
            "DELETE FROM dbo.Documentos OUTPUT DELETED.DocId, DELETED.Version WHERE DocId = %s",
            (doc_id,),
        ).fetchone()
        return Change("delete", int(row[0]), int(row[1]) + 1, self.now_utc())

    def sentinel(self, since: date) -> Decimal:
        """Valor total de vendas desde `since` (o sinal que o Daily Sales comparava)."""
        row = self._exec(
            "SELECT COALESCE(SUM(CASE WHEN Tipo = 'NC' THEN -Total ELSE Total END), 0) "
            "FROM dbo.Documentos WHERE Data >= %s", (since,),
        ).fetchone()
        return Decimal(row[0])

    def aggregates(self, since: date) -> list[tuple[int, date, Decimal, int]]:
        rows = self._exec(
            "SELECT VendedorId, Data, SUM(CASE WHEN Tipo = 'NC' THEN -Total ELSE Total END), COUNT(*) "
            "FROM dbo.Documentos WHERE Data >= %s GROUP BY VendedorId, Data ORDER BY VendedorId, Data",
            (since,),
        ).fetchall()
        return [(int(v), d, Decimal(t), int(n)) for v, d, t, n in rows]


class FakeSource:
    def __init__(self, now: Callable[[], datetime]):
        self._now = now
        self._ids = itertools.count(1)
        self.docs: dict[int, dict] = {}

    def now_utc(self) -> datetime:
        return self._now()

    def insert_doc(self, tipo, data, vendedor, cliente, total) -> Change:
        i = next(self._ids)
        self.docs[i] = dict(tipo=tipo, data=data, vendedor=vendedor, cliente=cliente,
                            total=Decimal(total), version=1)
        return Change("insert", i, 1, self._now())

    def _touch(self, doc_id: int, op: str = "update") -> Change:
        d = self.docs[doc_id]
        d["version"] += 1
        return Change(op, doc_id, d["version"], self._now())

    def update_total(self, doc_id, total) -> Change:
        self.docs[doc_id]["total"] = Decimal(total)
        return self._touch(doc_id)

    def update_vendedor(self, doc_id, vendedor) -> Change:
        self.docs[doc_id]["vendedor"] = vendedor
        return self._touch(doc_id)

    def delete_doc(self, doc_id) -> Change:
        v = self.docs.pop(doc_id)["version"]
        return Change("delete", doc_id, v + 1, self._now())

    def sentinel(self, since) -> Decimal:
        return sum((signed(d["tipo"], d["total"]) for d in self.docs.values() if d["data"] >= since),
                   Decimal(0))

    def aggregates(self, since):
        acc: dict[tuple[int, date], list] = {}
        for d in self.docs.values():
            if d["data"] >= since:
                a = acc.setdefault((d["vendedor"], d["data"]), [Decimal(0), 0])
                a[0] += signed(d["tipo"], d["total"])
                a[1] += 1
        return [(v, dt, t, n) for (v, dt), (t, n) in sorted(acc.items())]
