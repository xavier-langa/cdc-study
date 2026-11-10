# Estudo: polling com sentinela vs CDC (SQL Server)

Pergunta: para um painel de vendas com actualização a cada minuto, o que ganha e o que perde a
captura de mudanças por log (CDC + Debezium) face ao polling com valor-sentinela?

Este repositório tem, por agora, o **gerador de carga** e o **sistema A** (polling com sentinela).
O sistema B (Debezium) ainda não existe.

## Estado de validação (ler primeiro)

- Testado: a lógica (perfil de carga, poller, análise de latência, comparação do estado final) com uma
  origem em memória (9 testes), e o `PostgresSink` contra um Postgres 16 real (1 teste, corre no GitHub
  Actions e localmente quando há Postgres acessível).
- **Não testado**: tudo o que fala com SQL Server (`MssqlSource`, T-SQL, `sql/*.sql`) e o `docker-compose.yml`.
  Foram escritos sem acesso a um SQL Server. Espera correcções na primeira execução.

## Correr no GitHub Codespaces (sem instalar nada)

1. No GitHub, abre o repositório e escolhe **Code → Codespaces → Create codespace on main**.
2. Espera que o ambiente arranque (Python 3.12 e Docker já vêm na configuração `.devcontainer`).
3. No terminal do Codespace, segue a secção "Como correr" abaixo. Abre dois terminais para a parte do sistema A e do gerador.
4. Quando acabares, **pára o Codespace** (Codespaces → Stop). Parado, não gasta horas de computação, mas o armazenamento continua a contar enquanto o Codespace existir. Apaga os que já não usas.

Notas:
- A configuração pede 2 núcleos e 8 GB de memória. O sistema A (SQL Server e Postgres) deve caber; para o sistema B (Debezium) pode ser preciso uma máquina de 4 núcleos, que gasta a quota mais depressa.
- O SQL Server em Docker precisa de pelo menos 2 GB de memória livre.
- Os ficheiros de `runs/` não vão para o Git (ver `.gitignore`). Descarrega os resultados que quiseres guardar, ou copia-os para uma pasta versionada.
- As palavras-passe do `sa` e do Postgres no `docker-compose.yml` são só para este ambiente de prática. Não uses a mesma noutro sítio.
- O workflow `.github/workflows/tests.yml` corre os 10 testes (lógica e PostgresSink) a cada `push`. Não toca no SQL Server.

## Como correr

```bash
docker compose up -d sqlserver sql-init postgres # origem SalesSim (sem CDC) e destino Postgres
pip install -r requirements.txt

# terminal 1: sistema A durante 30 min, poll a cada 60 s
python -m cdcstudy.system_a --interval 60 --duration-min 30 --sink postgres --out runs/a.jsonl

# terminal 2: tráfego contínuo (30 000 docs/mês) e, depois, um cenário
python -m cdcstudy.generator continuous --duration-min 25 --events runs/events.jsonl
python -m cdcstudy.generator scenario compensating_pair --repeat 20 --events runs/events.jsonl

# no fim, com a origem parada, gravar a verdade e analisar
python -m cdcstudy.generator truth --out runs/truth.json
python -m cdcstudy.analyze --events runs/events.jsonl --writes runs/a.jsonl --truth runs/truth.json
```

Variáveis: `MSSQL_HOST`, `MSSQL_PORT`, `MSSQL_SA_PASSWORD` (origem); `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DB` (destino, por defeito o Postgres do `docker-compose.yml`).

## Regras do estudo

1. O CDC só pode estar activo nas medições do sistema B. `sql/02_enable_cdc.sql` não corre no arranque,
   porque o trabalho do SQL Server Agent com o CDC alteraria a carga medida no sistema A.
2. Para medir o sistema A, limpa a tabela entre execuções (`TRUNCATE TABLE dbo.Documentos`) e apaga `runs/*`.
3. Compara sempre as mesmas condições (mesma taxa, mesma duração, mesmos cenários, mesma semente `--seed`).

## O que cada medida significa

- **Latência de visibilidade**: um evento com `changed_at = t` fica visível no primeiro *refresh* com
  `read_at >= t`; latência = `written_at` desse refresh − t. Heartbeats não contam.
  `changed_at` e `read_at` vêm do relógio do SQL Server.
- **Escritas ao destino**: soma de `docs_written` (refresh + heartbeat). Para custo, multiplica pelos preços
  oficiais do destino.
- **Queries à origem**: contador do poller (1 por passagem + 1 por refresh). Não é CPU do SQL Server;
  essa medição (contadores de desempenho/DMVs) ainda não está implementada.
- **Estado final**: compara os agregados (vendedor, dia) do destino com os da origem.

## Pressupostos a confirmar

- `HOURLY_WEIGHTS` em `generator.py` é um perfil inventado; substitui pela forma do dia que conheces.
- No Daily Sales original, o refresh reescrevia todos os documentos? (`--write-mode all|changed`)
- `changed_at` é o instante da instrução (autocommit), não do commit na origem; a diferença deve ser de milissegundos.

## Cenários (hipóteses a verificar com as medições)

| Cenário | O que se espera no sistema A |
|---|---|
| `compensating_pair` | FT e NC do mesmo valor na mesma janela: o sentinela não muda, o destino fica desactualizado |
| `reassign_vendedor` | a venda muda de vendedor, o total não muda: o destino fica errado nos dois vendedores |
| `insert_then_delete` | o apagamento muda o total: é detectado |
| `multi_update` | só o estado final é visto (correcto nos agregados) |
