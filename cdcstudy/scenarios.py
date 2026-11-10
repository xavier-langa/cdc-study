"""Cenários escritos à mão. Cada um repete-se `repeat` vezes, espaçados para que as repetições
sejam independentes do relógio do poller (que corre a cada intervalo fixo).

Resultado esperado no sistema A (polling com sentinela = total de vendas do mês):
- compensating_pair   FT e NC do mesmo valor na mesma janela -> o sentinela não muda -> A não refresca
                      -> n_docs desse vendedor/dia fica desactualizado até outra alteração mexer no total.
- reassign_vendedor   a venda passa de vendedor, total igual -> sentinela igual -> A não refresca
                      -> o vendedor antigo continua a "ter" a venda até outra alteração mexer no total.
- insert_then_delete  insere e, depois de A ter refrescado, apaga -> o total desce -> A refresca (apagamentos são vistos).
- multi_update        insere e altera 3 vezes em 3 s -> A só vê o estado final (correcto nos agregados;
                      um consumidor por eventos veria 4 eventos).
Estas são hipóteses de desenho, a confirmar com as medições.
"""
from __future__ import annotations

import time
from decimal import Decimal

from .generator import EventLog, local_today


def _total(rng) -> Decimal:
    return Decimal(f"{rng.uniform(100, 3000):.2f}")


def compensating_pair(source, log: EventLog, rng, *, repeat=20, gap_s=2.0, spacing_s=90.0,
                      sleep=time.sleep) -> int:
    n = 0
    for i in range(repeat):
        v, c, tot, day = rng.randint(1, 24), rng.randint(1, 200), _total(rng), local_today(source)
        log.log("compensating_pair", source.insert_doc("FT", day, v, c, tot), rep=i)
        sleep(gap_s)
        log.log("compensating_pair", source.insert_doc("NC", day, v, c, tot), rep=i)
        n += 2
        sleep(spacing_s)
    return n


def reassign_vendedor(source, log: EventLog, rng, *, repeat=20, settle_s=75.0, spacing_s=75.0,
                      sleep=time.sleep) -> int:
    n = 0
    for i in range(repeat):
        v1 = rng.randint(1, 24)
        v2 = v1 % 24 + 1
        ch = source.insert_doc("FT", local_today(source), v1, rng.randint(1, 200), _total(rng))
        log.log("reassign_vendedor", ch, rep=i)
        sleep(settle_s)  # dá tempo ao sistema A de refrescar com o insert
        log.log("reassign_vendedor", source.update_vendedor(ch.doc_id, v2), rep=i)
        n += 2
        sleep(spacing_s)
    return n


def insert_then_delete(source, log: EventLog, rng, *, repeat=20, settle_s=75.0, spacing_s=75.0,
                       sleep=time.sleep) -> int:
    n = 0
    for i in range(repeat):
        ch = source.insert_doc("FT", local_today(source), rng.randint(1, 24), rng.randint(1, 200), _total(rng))
        log.log("insert_then_delete", ch, rep=i)
        sleep(settle_s)
        log.log("insert_then_delete", source.delete_doc(ch.doc_id), rep=i)
        n += 2
        sleep(spacing_s)
    return n


def multi_update(source, log: EventLog, rng, *, repeat=20, spacing_s=75.0, sleep=time.sleep) -> int:
    n = 0
    for i in range(repeat):
        ch = source.insert_doc("FT", local_today(source), rng.randint(1, 24), rng.randint(1, 200), _total(rng))
        log.log("multi_update", ch, rep=i)
        n += 1
        for _ in range(3):
            sleep(1.0)
            log.log("multi_update", source.update_total(ch.doc_id, _total(rng)), rep=i)
            n += 1
        sleep(spacing_s)
    return n


SCENARIOS = {
    "compensating_pair": compensating_pair,
    "reassign_vendedor": reassign_vendedor,
    "insert_then_delete": insert_then_delete,
    "multi_update": multi_update,
}
