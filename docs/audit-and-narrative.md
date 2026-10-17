# Auditoria e explicabilidade (P5)

Dono: P5 (@Leo3107). Estado: implementado e testado **com mocks** (previsor/detector do
backend, alerta inventado, HTTP do watsonx falso). Contrato D `Proposed` até a equipe
confirmar. Nada aqui foi rodado com dados reais do BattLeDIM nem contra o watsonx.ai de verdade.

| O quê | Onde |
|---|---|
| Log encadeado (`AuditSink` real) | `src/sentinel/audit/chain.py` — `HashChainAuditSink`, `audit_path_for` |
| Verificação por linha de comando | `src/sentinel/audit/verify.py` — `python -m sentinel.audit.verify` |
| Fatos estruturados + texto determinístico | `src/sentinel/narrative/facts.py` |
| Validação do texto gerado | `src/sentinel/narrative/check.py` |
| LLM injetável + fallback | `src/sentinel/narrative/explain.py` |
| Granite no watsonx.ai | `src/sentinel/narrative/watsonx.py` — `WatsonxNarrator` |
| Granite na própria LinuxONE (GGUF via llama.cpp) | `src/sentinel/narrative/local.py` — `LlamaCppNarrator` (no processo) e `LocalGraniteNarrator` (servidor) |
| Notebook para a VM (descoberta + Granite local) | `notebooks/p5_audit/p5_linuxone_granite.ipynb` |
| Explicar um alerta pela linha de comando | `src/sentinel/narrative/__main__.py` — `python -m sentinel.narrative [--llamacpp \| --local \| --watsonx]` |
| API com a cadeia ligada (temporário, até BE-013) | `src/sentinel/audit/devserver.py` |
| Alerta inventado para testes/notebooks | `src/sentinel/narrative/mock.py` (`is_mock=True`) |
| Testes | `tests/test_audit_chain.py`, `tests/test_narrative.py`, `tests/test_watsonx.py` |
| Demonstração | `notebooks/p5_audit/p5_auditoria_narrativa.ipynb` |

O sink entra no pipeline pela injeção que já existe em `build_pipeline(audit_sink=...)`; os
arquivos do backend não foram alterados.

## Decisões da equipe (2026-10-17)

| Tema | Decisão |
|---|---|
| LLM da narrativa | watsonx.ai com modelo Granite (`WatsonxNarrator`) |
| Q-AUD-03 falha ao gravar auditoria | **fail-open**, como o pipeline já faz: o alerta é mantido, `audit="failed"` e `audit_failures` aparecem no resultado e em `/v1/status` |
| Q-AUD-02 onde fica o log | `~/shared/civicwater/audit/<run>.jsonl` na VM, fora dos clones do Git (`audit_path_for(run)`) |
| Idioma da explicação | **inglês** por padrão (`DEFAULT_LANGUAGE = "en"`); `language="pt"` continua disponível |

A decisão de fail-open ainda precisa ser registrada pelo backend no ADR-006 e em
`docs/questions-for-team.md` (arquivos do P3).

## Contrato D — registro de auditoria v1.0

Uma linha JSON por evento recebido do pipeline (`AuditEvent`: `alert.raised`,
`window.scored`, `window.not_scored`):

| Campo | Conteúdo |
|---|---|
| `record_version` | `"1.0"` |
| `seq` | 0, 1, 2… atribuído pelo sink; continua após reinício |
| `recorded_at` | relógio do servidor ao gravar (UTC, `Z`) |
| `type`, `window_end`, `alert_id` | do `AuditEvent` |
| `payload` | do `AuditEvent` (para `alert.raised`: o alerta **sem** o campo `audit`) |
| `payload_sha256` | `SHA-256(json_canônico(payload))` |
| `prev_hash` | `hash` do registro anterior; o primeiro usa 64 zeros |
| `hash` | `SHA-256(prev_hash ‖ json_canônico(registro sem prev_hash e hash))` |

JSON canônico: `json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)` em
ASCII, a mesma regra de `SensorWindow.content_hash`. Floats seguem o `repr` do Python
(igual em x86 e s390x). Um verificador em outra linguagem precisa reproduzir essa
serialização; se isso for necessário, migrar para RFC 8785 (JCS) numa v2.

`AuditRef` devolvido ao pipeline: `{seq, hash}`. O alerta servido em `GET /v1/alerts/{id}`
carrega esse par em `audit`.

**Ligação alerta ↔ registro (Q-AUD-05):** `payload_sha256` do registro `alert.raised` é o hash
do alerta sem `audit`. Qualquer um com o JSON do alerta confere com `verify_alert(alerta,
registro)` ou `alert_payload_sha256(alerta)`; testado inclusive com o JSON vindo da API.

**Garantias do sink:** append-only; thread-safe; cada linha passa por `flush` + `fsync` antes de
`append` retornar; falha de escrita levanta `AuditError` sem consumir `seq` e sem deixar linha
pela metade; ao iniciar, o arquivo existente é verificado e um arquivo quebrado **nunca** é
estendido (o início falha com `AuditError`).

**Limitação:** quem pode reescrever o arquivo inteiro consegue recalcular uma cadeia
consistente. A defesa é a âncora: ao fim de cada replay, anotar `sink.head` (`seq` + `hash`)
fora da VM e verificar com `--anchor-seq/--anchor-hash`. Âncora antiga continua válida quando
a cadeia cresce; truncamento abaixo dela é detectado.

## Explicação do alerta

1. `alert_facts(alert)` — JSON só com dados do alerta (região, sensores, desvios, z, início,
   CUSUM, top-3 contribuições por sensor, modelos, `audit_seq`). É a explicação estruturada e
   a única entrada do LLM.
2. `render_text(alert)` — texto determinístico em inglês (ou `"pt"`); sempre disponível.
3. `explain(alert, narrador)` — chama o LLM. O texto só é aceito se `check_narrative` não
   encontrar nada fora do payload; senão, ou se o modelo falhar/demorar/vier vazio, volta o
   template com `fallback_reason`.

`check_narrative` rejeita: sensor, região ou ID que não estão no alerta; número que não bate
com um valor do alerta (arredondado às casas escritas, por valor absoluto); data ou horário que
não são `window_end`, `suspected_onset` ou `since`; frases que confirmam ou descartam
vazamento ("confirmed leak", "definitely", "vazamento confirmado"…); alerta mock sem o aviso
"[SIMULATION]"/"[SIMULAÇÃO]". Não verifica números por extenso ("three") nem unidades.

`Explanation` carrega `alert_payload_sha256` e `audit`, então a explicação fica presa à versão
auditada do alerta. O LLM nunca decide se existe alerta: isso é do detector (P4).

## watsonx.ai / Granite

`WatsonxNarrator` usa só a biblioteca padrão (nenhum pacote a instalar no LinuxONE):

1. token IAM: `POST https://iam.cloud.ibm.com/identity/token` (grant
   `urn:ibm:params:oauth:grant-type:apikey`), reaproveitado até 60 s antes de expirar;
2. chat: `POST {WATSONX_URL}/ml/v1/text/chat?version=2024-10-08` com `model_id`,
   `project_id`, `messages`, `max_tokens=300`, `temperature=0`; o texto vem de
   `choices[0].message.content`.

| Variável | Conteúdo |
|---|---|
| `WATSONX_APIKEY` | API key do IBM Cloud — **segredo**: nunca no Git, no notebook ou em logs |
| `WATSONX_URL` | endpoint regional, ex. `https://us-south.ml.cloud.ibm.com` |
| `WATSONX_PROJECT_ID` | projeto do watsonx.ai |
| `WATSONX_MODEL_ID` | modelo Granite de chat; em 2026-10-17 só `ibm/granite-4-h-small`, só em us-south e eu-de (ver abaixo) |
| `WATSONX_API_VERSION` | opcional, padrão `2024-10-08` |
| `WATSONX_TIMEOUT_S` | opcional, padrão 20 |

Uso: `explain(alert, WatsonxNarrator.from_env())`. Erros (HTTP, rede, timeout, resposta
inesperada) viram `WatsonxError`, e `explain` cai no template com o motivo; a API key não
aparece em `repr`, mensagens de erro nem `fallback_reason`. Pendente: a chamada real nunca foi
executada (sem credenciais aqui) e é preciso confirmar que a VM alcança `*.cloud.ibm.com`.

Modelos de chat por região (consulta pública, sem token; repetir antes da demo):

```bash
curl -s "https://us-south.ml.cloud.ibm.com/ml/v1/foundation_model_specs?version=2024-10-10&filters=function_text_chat&limit=200" | python -m json.tool | grep model_id
```

Resultado em 2026-10-17: `ibm/granite-4-h-small` em us-south (Dallas) e eu-de (Frankfurt);
nenhum Granite de chat em eu-gb, jp-tok, au-syd e ca-tor. O projeto watsonx.ai precisa estar
numa dessas duas regiões e ter um serviço watsonx.ai Runtime (Watson Machine Learning)
associado.

## Granite na própria LinuxONE (sem nuvem)

O que a VM da equipe tem (seção 1 de `p5_linuxone_granite.ipynb`, 2026-10-17):

| Item | Resultado |
|---|---|
| Máquina | s390x, 3 CPUs, 8,2 GB de RAM (3,2 GB livres no momento), NNPA (Telum) visível |
| Modelo | `~/granite-4.0-h-350m-base-be.f16.gguf`, 685 MB, GGUF v3 big-endian |
| Runtime | `llama_cpp` (llama-cpp-python) **instalado no kernel do Jupyter**; sem `llama-server`; `gcc` e `cmake` presentes |
| Exemplo da IBM | `~/Loan_Approval_Demo.ipynb` menciona o Granite |

O modelo é o Granite 4.0 H 350M **base**: pequeno e **não** ajustado para instruções. Por isso o
prompt traz dois exemplos inventados e depois os fatos do alerta (`prompt_for`). Se o modelo
copiar um exemplo ou inventar algo, `check_narrative` recusa e entra o template.

**Caminho principal: `LlamaCppNarrator`.** Carrega o GGUF dentro do processo Python com o
`llama_cpp`, uma vez, e serializa as chamadas. `close()` libera a memória.

| Variável | Padrão |
|---|---|
| `LOCAL_LLM_MODEL_PATH` | `~/granite-4.0-h-350m-base-be.f16.gguf` |
| `LOCAL_LLM_THREADS` | 1 (3 vCPUs para a equipe) |
| `LOCAL_LLM_CTX` | 2048 tokens |

Teste: `python -m sentinel.narrative --llamacpp`. A saída 0 significa texto aceito, e o
resultado mostra `model_load_s` e `elapsed_s`. O notebook `p5_linuxone_granite.ipynb` carrega o
modelo, explica um alerta inventado e um do pipeline (en/pt) e libera a memória.

**Alternativa com servidor: `LocalGraniteNarrator`.** Usa `POST {LOCAL_LLM_URL}/v1/completions`
(`LOCAL_LLM_URL`, `LOCAL_LLM_MODEL`, `LOCAL_LLM_TIMEOUT_S`) com um `llama-server` ou
`python -m llama_cpp.server`. Serve para quando o modelo precisar ficar fora do processo da
API. Hoje a VM não tem `llama-server`.

**Comparação:** o 350M base roda localmente, sem chave e sem rede, mas deve ter taxa de
aceitação menor. O `ibm/granite-4-h-small` no watsonx é maior e ajustado para instruções, mas
depende de rede e de credenciais. As duas opções passam pela mesma validação; a escolha final
depende da taxa de aceitação e dos tempos medidos na VM.

## Como testar no notebook local (Windows, PowerShell)

Na raiz do clone. `uv` não está instalado nesta máquina; `python -m uv` evita depender do PATH.

```powershell
python -m pip install uv
python -m uv sync --locked
python -m uv run pytest tests/test_audit_chain.py tests/test_narrative.py tests/test_watsonx.py
python -m uv run pytest
```

Gates da CI: `python -m uv run ruff format --check src tests scripts`,
`python -m uv run ruff check src tests scripts`, `python -m uv run mypy`.

Notebook (no Windows use `python -m jupyter`; `uv run --with jupyter jupyter lab` falha com
"os error 3" neste caminho):

```powershell
python -m uv run --with jupyter python -m jupyter lab
```

Ponta a ponta pela API (dois terminais; `data/` é ignorado pelo Git):

```powershell
$env:SENTINEL_AUDIT_RUN="local-test"; $env:SENTINEL_AUDIT_DIR="data/audit"; python -m uv run uvicorn sentinel.audit.devserver:create_app --factory --port 8000
```

```powershell
python -m uv run python scripts/make_synthetic.py data/synthetic.jsonl --count 288 --leak-at 200
python -m uv run python scripts/replay.py data/synthetic.jsonl
curl.exe -s http://127.0.0.1:8000/v1/alerts
curl.exe -s http://127.0.0.1:8000/v1/alerts/<alert_id> -o data/alert.json
python -m uv run python -m sentinel.audit.verify data/audit/local-test.jsonl --alert data/alert.json
python -m uv run python -m sentinel.narrative --alert data/alert.json
```

Esperado (mocks): 288 janelas aceitas, 1 alerta, cadeia com 289 registros `ok: true`, alerta
`ok: true`, explicação `source: template` com `[SIMULATION]`. Ao reiniciar o servidor, a mesma
cadeia continua; apague `data/audit/local-test.jsonl` para começar do zero.

Granite (precisa das credenciais; a chave é digitada sem aparecer nem ficar no histórico):

```powershell
$env:WATSONX_APIKEY = [Net.NetworkCredential]::new('', (Read-Host 'API key' -AsSecureString)).Password
$env:WATSONX_URL="https://us-south.ml.cloud.ibm.com"; $env:WATSONX_PROJECT_ID="<project id>"; $env:WATSONX_MODEL_ID="ibm/granite-4-h-small"
python -m uv run python -m sentinel.narrative --watsonx
python -m uv run python -m sentinel.narrative --alert data/alert.json --watsonx
```

Código de saída: 0 = texto do Granite aceito; 1 = Granite chamado mas texto recusado ou erro
(ver `fallback_reason` e `check`); 2 = variável faltando.

## Como fazer funcionar na VM LinuxONE

1. **Levar o código:** branch própria (`feat/audit-chain-narrative`), `nbstripout --install`,
   commit, push e PR para `main` com revisão de quem é afetado (P3 por `devserver.py` e pela
   integração).
2. **No seu clone da VM** (`~/shared/civicwater/users/<nome>/repo`): `git fetch` e trocar para
   a branch. Sem `pip install -e`: o notebook e os comandos usam `PYTHONPATH=src` com o Python
   de `/opt/venv`.
3. **Testes na VM:**
   `PYTHONPATH=src python -m pytest -q tests/test_audit_chain.py tests/test_narrative.py tests/test_watsonx.py`.
   `test_audit_chain.py` usa o `TestClient` do FastAPI (precisa de `httpx2` em `/opt/venv`); se
   faltar `pytest`/`httpx2`, pedir a quem administra o ambiente.
4. **Rede:** `curl -s -o /dev/null -w "%{http_code}\n" https://iam.cloud.ibm.com/identity/token`
   deve responder um código HTTP (400 = alcançável). Timeout ou erro de DNS = saída bloqueada;
   aí a narrativa fica sempre no template e é preciso liberar `iam.cloud.ibm.com` e
   `us-south.ml.cloud.ibm.com` (ou `eu-de`).
5. **Credenciais:** a VM tem um único usuário Linux, então qualquer arquivo ou `.bashrc` é lido
   por todos. Defina a chave só na sessão (`read -rs WATSONX_APIKEY && export WATSONX_APIKEY`
   no terminal; `getpass` no notebook). Prefira uma API key de Service ID com acesso só ao
   projeto, e revogue depois do evento.
6. **Teste do Granite na VM:** `PYTHONPATH=src python -m sentinel.narrative --watsonx`
   (saída 0 esperada).
7. **API compartilhada com a cadeia** (depois do merge, na cópia de integração, combinado com o
   P3, um único worker):
   `cd ~/shared/civicwater/main && SENTINEL_AUDIT_RUN=replay-2019 uvicorn sentinel.audit.devserver:create_app --factory --host 0.0.0.0 --port 8000`.
   Quando o BE-013 existir, trocar por `SENTINEL_AUDIT_SINK=chain` e apagar `devserver.py`.
8. **Depois do replay:**
   `python -m sentinel.audit.verify ~/shared/civicwater/audit/replay-2019.jsonl` e guardar o
   `head` (`seq` + `hash`) **fora da VM** (PR, chat da equipe, seu computador). Na demo,
   verificar com `--anchor-seq/--anchor-hash`.
9. **Alertas reais:** só deixam de ser mock quando P1 (IDs reais, BE-010), P2 (modelo) e P4
   (CUSUM/região) entregarem; o dashboard só mostra a explicação quando P3 criar
   `GET /v1/alerts/{id}/explanation`.

## Perguntas ainda em aberto

| Pergunta | Proposta da P5 |
|---|---|
| Q-AUD-01 porta e tipos de evento | Aceitos como estão. |
| Q-AUD-04 `GET /v1/audit` | Útil para o dashboard (P6). Proposta: `GET /v1/audit?offset&limit` (registros) e `GET /v1/audit/verify` (`ChainVerification`), só leitura. |
| Q-AUD-05 circularidade | Resolvida como no ADR-006: hash do alerta sem `audit`. |
| DEP-AUD-04 onde fica a narrativa | Gerada sob demanda a partir do alerta; proposta `GET /v1/alerts/{id}/explanation` → `Explanation`. Opcional: evento `explanation.generated` na cadeia (exige mudar `AuditEventType`). |

## O que depende de outras pessoas

- **P3 (backend), BE-013:** aceitar `SENTINEL_AUDIT_SINK=chain` e um caminho/execução
  (`SENTINEL_AUDIT_PATH` ou `SENTINEL_AUDIT_RUN`) em `config.py` e criar o sink em
  `main.build_pipeline`, por exemplo `HashChainAuditSink(audit_path_for(run))`. Endpoints de
  leitura acima. Registrar as variáveis `WATSONX_*` (sem valores) em `.env.example`.
- **P4 (detecção):** região, `suspected_onset`, `cusum` e `since` reais no `AlertCandidate`; a
  explicação já os usa quando existem.
- **P2 (ML):** `contributions` (TreeSHAP) no `ForecastResult`; o backend já as restringe aos
  sensores do alerta.
- **P6 (dashboard):** exibir `explanation.text`, `source`/`fallback_reason`, `audit.seq` e o
  resultado da verificação.

## Verificação (2026-10-17, Windows x86_64, Python 3.13.5, dependências do `uv.lock`)

| Comando | Resultado |
|---|---|
| `ruff format --check` + `ruff check` (`src tests scripts`) | ok |
| `mypy` (configuração do projeto, 69 arquivos) | sem problemas |
| `pytest` (suíte inteira) | 186 passed (89 do backend + 97 da P5) |
| `p5_linuxone_granite.ipynb` executado (Windows) contra um servidor de completions falso | 0 erros; texto aceito no alerta inventado, exemplo copiado recusado no alerta do pipeline |
| `python -m sentinel.narrative --local` contra o mesmo servidor falso (HTTP real) | `source: llm` |
| `p5_linuxone_granite.ipynb` (versão `llama_cpp` no kernel) executado no Windows com um `llama_cpp` falso | 0 erros; carrega, aceita o texto válido, recusa o exemplo copiado (en/pt), descarrega |
| Granite GGUF real na VM (`--llamacpp`) | **NOT_RUN** — runtime identificado (`llama_cpp` no kernel), execução pendente |
| LinuxONE VM (s390x), branch `leo/local-version` @ `033959d`: `python -m pytest -q tests/test_audit_chain.py tests/test_narrative.py tests/test_local_granite.py tests/test_watsonx.py` | **93 passed** in 0.27 s |
| `jupyter nbconvert --execute` do notebook (kernel real, sem `WATSONX_*`) | 19 células, 0 erros; Granite pulado |
| Ponta a ponta local: `devserver` + `make_synthetic` + `replay` + `verify --alert` + `sentinel.narrative --alert` | 1 alerta mock; cadeia 289 registros ok; alerta ok; template |
| `foundation_model_specs` (público) por região | Granite de chat só em us-south e eu-de (`ibm/granite-4-h-small`) |
| LinuxONE VM (s390x): `p5_auditoria_narrativa.ipynb` completo no Jupyter da VM | todas as seções ok: cadeia de 289 registros válida, adulterações detectadas, alerta `A-20190314T1640Z-1` (mesmo ID e mesmos desvios do x86) ligado ao registro seq 200, LLM falso aceito/recusado como esperado |
| Paridade x86 × s390x do JSON canônico | `payload_sha256` de `{"i": 1, "status": "scored"}` = `993ee9cc…20d79f` nas duas plataformas |
| `p5_linuxone_granite.ipynb` e API ponta a ponta na VM, dados reais, watsonx.ai com credenciais | **NOT_RUN** |
