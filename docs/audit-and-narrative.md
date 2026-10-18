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
| API com a cadeia e as explicações ligadas (temporário, até BE-013/Q-AUD-07) | `src/sentinel/audit/devserver.py` |
| Explicações geradas em segundo plano, guardadas e auditadas | `src/sentinel/narrative/store.py` — `ExplanationService`, `ExplanationStore` |
| Alerta inventado para testes/notebooks | `src/sentinel/narrative/mock.py` (`is_mock=True`) |
| Testes | `tests/test_audit_chain.py`, `tests/test_narrative.py`, `tests/test_watsonx.py` |
| Demonstração | `notebooks/p5_audit/p5_auditoria_narrativa.ipynb` |

O sink entra no pipeline pela injeção que já existe em `build_pipeline(audit_sink=...)`; os
arquivos do backend não foram alterados.

## Decisões da equipe (2026-10-17)

| Tema | Decisão |
|---|---|
| LLM da narrativa | watsonx.ai com modelo Granite (`WatsonxNarrator`) |
| Q-AUD-03 falha ao gravar auditoria | **fail-closed** (decisão da equipe em `team-workflow`, implementada pelo P3 na `main`, ADR-006). O alerta só é servido depois que o `alert.raised` foi gravado; eventos que falham esperam numa fila ordenada e são reenviados antes de cada janela nova; com a fila cheia (`SENTINEL_AUDIT_PENDING_CAPACITY`), a resposta é 503 `audit_backlog_full`. Status da janela: `recorded`, `pending` ou `disabled`. |
| Q-AUD-02 onde fica o log | `~/shared/civicwater/audit/<run>.jsonl` na VM, fora dos clones do Git (`audit_path_for(run)`) |
| Idioma da explicação | **inglês** por padrão (`DEFAULT_LANGUAGE = "en"`); `language="pt"` continua disponível |
| Q-DET-02 sinal do z | `deviation_m = observado − esperado` (negativo numa queda) e `z = (esperado − observado)/σ` (positivo numa queda). O alerta inventado e os exemplos do prompt da P5 seguem essa convenção; a validação compara valores absolutos. |
| Alerta 1.1 (ADR-011, P4) | Novo campo opcional `candidate_pipe` (ID de tubo, por exemplo `p147`). Regiões reais do L-Town (por exemplo `Z3-R1`). A P5 mostra o tubo como "Candidate pipe (not a confirmed location)" / "Tubo candidato (não é uma localização confirmada)"; a validação aceita esse ID e recusa outro tubo. O prompt do modelo pequeno não inclui o tubo (mesmo motivo das contribuições). |

## Quando o `[SIMULATION]` some

Um alerta só deixa de ser simulado quando o previsor **e** o detector são reais (`is_mock=false`).
Situação em 2026-10-18, depois do merge da `main`:

| Peça | Situação |
|---|---|
| Previsor real (P2) | **Existe:** XGBoost em `~/shared/civicwater/models/xgboost-33@0.1.0` na VM (`SENTINEL_FORECASTER=xgboost`), `is_mock=false`. |
| Dados reais de 2019 (P1) | **Existem:** o replay inteiro de 2019 passa na validação (`docs/runbook-vm.md`). |
| Regiões e rede de tubos (P4) | **Existem:** `config/regions.battledim.json` e `l-town.json` no diretório de dados. |
| Calibração do CUSUM (P4) | **Falta.** O detector só deixa de ser mock com `provenance = "battledim-2018-oof"`, calibrado com os resíduos fora da amostra de 2018 **do mesmo previsor** (DEP-DET-05). Para o `xgboost-33@0.1.0` essa calibração não existe; as que existem são `synthetic` ou `dev-baseline-2018`, que forçam `is_mock=true`. |

Ou seja, falta o P2 entregar os resíduos fora da amostra de 2018 do XGBoost, e o P4 rodar a
calibração e commitar `config/calibration.<previsor>.json`. Com o XGBoost e o detector mock, nenhum
alerta é gerado (σ de 1 m contra resíduos de milímetros). Quando a calibração existir, a P5 não
muda nada: o aviso some sozinho, e esse caminho já está testado.

Uma versão anterior deste documento registrava fail-open, porque era o que o backend fazia na
época. A `main` apontou a contradição (Q-DOC-01); este documento agora segue a `main`.

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

Com o fail-closed, o pipeline reenvia o evento que falhou, então `AuditError` precisa
significar "não gravado". Por isso o sink confere, antes de cada gravação, se o arquivo ainda
tem o tamanho que ele mesmo deixou. Se outro processo escreveu no arquivo, ou se uma gravação
falha não pôde ser desfeita, o sink recusa com `AuditError` em vez de gravar um segundo
registro com o mesmo `seq`. O pipeline segura os alertas e o problema fica visível; reiniciar
o processo faz a verificação de novo. Use **um arquivo por processo**.

**Limitação:** quem pode reescrever o arquivo inteiro consegue recalcular uma cadeia
consistente. A defesa é a âncora: ao fim de cada replay, anotar `sink.head` (`seq` + `hash`)
fora da VM e verificar com `--anchor-seq/--anchor-hash`. Âncora antiga continua válida quando
a cadeia cresce; truncamento abaixo dela é detectado.

## Explicações guardadas e auditadas (geradas em segundo plano)

O Granite leva ~30 s por explicação na VM, então a explicação não pode ser gerada quando o
operador abre o alerta. Além disso, o texto que o operador viu precisa ficar registrado.

**Como funciona:**
1. A cadeia de auditoria grava o `alert.raised`. O `ExplainingAuditSink` (no `devserver`) põe o
   alerta na fila do `ExplanationService`. Esse passo nunca faz a gravação falhar: com o
   fail-closed, uma falha ali faria o backend reenviar um evento que já está no log.
2. Um único worker em segundo plano chama `explain()` (Granite + validação + fallback), fora do
   pipeline das janelas, que continua com a mesma ordem e a mesma latência.
3. O resultado vai para `~/shared/civicwater/audit/<run>.explanations.jsonl`. É uma cadeia com
   o **mesmo formato** da auditoria (`type = "explanation.generated"`), verificável com
   `python -m sentinel.audit.verify`. O payload traz a `Explanation` inteira (texto, fatos,
   validação, texto bruto do modelo, modelo usado), `text_sha256`, `attempt` e
   `alert_payload_sha256`.
4. `verify_explanation(registro_da_explicação, registro_do_alerta)` confere três coisas: que a
   explicação é daquele `alert.raised`, da mesma versão do alerta, e que o texto bate com o hash
   gravado.

**Regras** (uma explicação por versão do alerta e idioma):
- Quando o modelo responde, ele é chamado **uma única vez**. Texto aceito e texto recusado são
  finais: com `temperature=0`, chamar de novo daria o mesmo texto.
- Quando o modelo **falha** (erro ou resposta vazia), o texto padrão é gravado e mostrado, e um
  novo `submit` tenta o modelo de novo, até `max_attempts` (3) chamadas no total.
- Tudo fica no log, e o registro mais recente de cada chave é o atual. Depois de um reinício, o
  serviço lê o log e não chama o modelo de novo.
- Se o log de explicações não puder ser gravado (por exemplo, alguém escreveu no arquivo), a
  explicação fica `pending` com o texto padrão, e um log quebrado nunca é estendido.

**Leitura** (implementação de referência para o Q-AUD-07, no `devserver`):
`GET /v1/alerts/{id}/explanation?language=en|pt` devolve `ExplanationStatus`:
- `status`: `pending` (com o texto padrão enquanto isso) ou `ready`;
- `source`, `text`, `generator`, `generated_at`, `fallback_reason`;
- `alert_payload_sha256` e `record` (`seq`/`hash` no log de explicações).

Alerta desconhecido devolve 404 `not_found`; idioma inválido, 422. Variáveis de ambiente:
`SENTINEL_NARRATOR` = `none` (padrão, só texto padrão) | `llamacpp` | `local` | `watsonx`, e
`SENTINEL_EXPLANATION_LANGUAGES` (padrão `en`).

Na VM, com o Granite carregado no processo da API (1 thread):

```bash
SENTINEL_AUDIT_RUN=leo-teste SENTINEL_NARRATOR=llamacpp SENTINEL_EXPLANATION_LANGUAGES=en uvicorn sentinel.audit.devserver:create_app --factory --host 0.0.0.0 --port $API_PORT
```

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
"[SIMULATION]"/"[SIMULAÇÃO]".

Também rejeita **número no lugar errado** (`misattributed_numbers`). Em cada trecho do texto
(separado por ". ", "; ", ": " ou quebra de linha):
- um desvio ou z precisa ser de um sensor citado no mesmo trecho;
- uma contribuição do previsor precisa vir com o nome da feature;
- valores globais (CUSUM, limiar, horas, contagem, seq, datas) podem aparecer em qualquer trecho.

Essa regra nasceu de um caso real na VM: o Granite 350M escreveu "P14 and P21 read 0.21 m and
0.17 m below", usando as contribuições de `hour_of_week` como se fossem desvios. Cada número
existia no payload, então a regra antiga aceitou. Limites que continuam: não detecta troca
entre dois sensores citados juntos ("P14 and P21 read 0.62 m and 0.91 m"), não confere números
por extenso ("three") nem unidades.

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

### Resultado na VM (2026-10-17, `p5_linuxone_granite.ipynb`, 1 thread)

| Medida | Valor |
|---|---|
| Runtime | `/opt/venv` Python 3.12.15, llama-cpp-python 0.3.36; `system info`: `zDNN : NNPA = 1`, `CPU : LLAMAFILE = 1, OPENMP = 1, REPACK = 1` |
| Carga do modelo | 11,1 s (primeira vez) · 0,5 s (arquivo já em cache) |
| Alerta inventado (en) | 41,0 s · **aceito, mas errado**: contribuições apresentadas como desvios (hoje recusado pela regra acima) |
| Alerta do pipeline (en) | 34,3 s · recusado: copiou a região `R2` do exemplo → template |
| Alerta do pipeline (pt) | 44,1 s · aceito: "[MOCK] 3 sensores abaixo o esperado de pressão." (verdadeiro, pobre) |

Os números do `system info` mostram que o build tem suporte a zDNN e detectou o NNPA. Não foi
medido quanto da inferência roda no acelerador.

**2ª rodada:** prompt enxuto (sem contribuições, resumo do detector, modelos e seq; números
com 2 casas; `max_tokens` 120) e a regra de números no lugar errado.

| Alerta | Tempo | Resultado |
|---|---|---|
| Inventado (en) | 34,1 s | recusado: não escreveu `[SIMULATION]` |
| Pipeline (en) | 31,5 s | recusado: copiou a região `R2` do exemplo e não escreveu `[SIMULATION]` |
| Pipeline (pt) | 26,9 s | recusado: copiou a região `R2` do exemplo |

Ficou 7–17 s mais rápido por explicação, e nenhum número foi parar no lugar errado. Correções
feitas depois dessa rodada:
- o aviso de simulação passou a ser colocado pelo código (`explain`), e saiu do prompt;
- o prompt mostra só exemplos com o mesmo formato do alerta (com ou sem região);
- `Explanation.model_text` guarda o texto bruto do modelo, mesmo quando recusado;
- `LlamaCppNarrator.last_usage` guarda os tokens lidos e escritos.

**3ª rodada:** aviso colocado pelo código, exemplos do mesmo formato e texto bruto guardado.

| Alerta | Tempo | Tokens (prompt + resposta) | Resultado |
|---|---|---|---|
| Inventado (en) | 33,6 s | 677 + 87 | **aceito e correto**: R4; P14/P21/P09 a 0,91/0,62/0,48 m; CUSUM 12,4/8,0 |
| Pipeline (en) | 26,0 s | 548 + 68 | **aceito e correto**: sem região; P03/P02/P01 a 5,06/4,96/4,95 m às 16:40 UTC |
| Pipeline (pt) | 28,9 s | 586 + 79 | **aceito e correto**: o mesmo, em português |

Conferi os três textos contra os alertas: valores, ordem dos sensores, região, horários e CUSUM
batem.

Uma imprecisão que a validação não pega: no alerta inventado o texto diz que os três sensores
estão abaixo "since 02:10", mas o P09 não tem `since`. O 02:10 é o início estimado do alerta.

**Para onde vai o tempo** (estimativa feita com as 3 medidas, só indicativa): ~0,026 s por
token de prompt (~38 tokens/s) e ~0,17 s por token gerado (~6 tokens/s). Ou seja, ~15–17 s
lendo o prompt e ~12–15 s escrevendo, com 1 thread.

Formas de reduzir:
- menos tokens de prompt (por exemplo, 1 exemplo em vez de 2; mudaria a qualidade, então teria
  de ser medido);
- 2 threads quando a VM estiver livre;
- reaproveitar o prefixo do prompt entre alertas do mesmo formato (não verificado para o
  modelo híbrido).

Mesmo com ~30 s, a explicação cabe folgada no intervalo de 300 s entre janelas, desde que seja
gerada em segundo plano e guardada.

Limites da amostra: 3 alertas, todos mock, e com `temperature=0` a mesma entrada dá o mesmo
texto. Para estimar a taxa de aceitação é preciso uma avaliação com mais alertas e formatos
variados.

**Conclusão e recomendação:**
- **A infraestrutura funciona no LinuxONE:** o modelo carrega, gera, a validação recusa o que
  está errado e o fallback entra.
- **A qualidade do Granite 350M *base* depende do prompt e da validação.**
  - 1ª rodada: 1 recusa, 1 texto errado aceito e 1 texto trivial.
  - Depois de prender os números ao sensor certo, colocar o aviso pelo código e usar exemplos
    do mesmo formato (3ª rodada): 3/3 aceitos e corretos.

  Ainda é uma amostra pequena e mock. O texto padrão continua sendo o fallback garantido; o texto
  do Granite é mostrado só quando aprovado pela validação.
- **Para texto melhor:** um Granite *instruct* em GGUF big-endian (por exemplo, 350M ou 1B
  instruct convertido com `convert_hf_to_gguf.py --bigendian`) ou o `ibm/granite-4-h-small` no
  watsonx.
- **Latência de 34–44 s por explicação com 1 thread:** a explicação precisa ser gerada em
  segundo plano, logo depois do alerta, e guardada. Não pode ser gerada quando o operador abre
  o alerta.

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
| Q-AUD-05 circularidade | Resolvida como no ADR-006: hash do alerta sem `audit`. |
| Q-AUD-06 leitura da cadeia por HTTP (substitui Q-AUD-04) | **De acordo com a proposta da `main`.** `GET /v1/audit?after_seq&limit` devolve os registros em ordem de `seq`, exatamente como gravados; `GET /v1/audit/head` devolve `{seq, hash}`; só leitura; o backend **não** devolve um booleano "verified", porque verificar é trabalho do cliente (`python -m sentinel.audit.verify`, `verify_records`). A P5 já fornece `HashChainAuditSink.records_after(after_seq, limit)` (lê o arquivo aos poucos) e `HashChainAuditSink.head`. As regras que o contrato deve citar estão no Contrato D acima: JSON canônico, `prev_hash` inicial com 64 zeros e a fórmula do `hash`. |
| Q-AUD-07 explicação por HTTP | **De acordo com o formato proposto.** A implementação de referência está no `devserver` (`GET /v1/alerts/{id}/explanation`, ver "Explicações guardadas e auditadas"), com `status` `pending`/`ready`, `text`, `generator` (string, nome do narrador; vira `ModelInfo` se o P3 preferir), `alert_payload_sha256`, `generated_at` e `record`. `disabled` fica para o backend usar quando não houver serviço de explicações. O P3 move a rota para `sentinel.api` e chama `ExplanationService.submit(alert)` quando o alerta é gravado. |
| Q-DOC-01 vocabulário de `audit` e fail-open no doc da P5 | Resolvido do lado da P5: a branch trouxe a `main` (merge, sem rebase, porque a branch já está publicada e o `AGENTS.md` proíbe `push --force`). `AuditStatus` agora é o da `main` (`recorded`, `pending`, `disabled`), e este doc registra fail-closed. |

## O que depende de outras pessoas

- **P3 (backend), BE-013:** aceitar `SENTINEL_AUDIT_SINK=chain` e um caminho/execução
  (`SENTINEL_AUDIT_PATH` ou `SENTINEL_AUDIT_RUN`) em `config.py` e criar o sink em
  `main.build_pipeline`, por exemplo `HashChainAuditSink(audit_path_for(run))`. Na `main`
  (depois do merge) isso ainda não existe: só `null`/`memory`. Endpoints de Q-AUD-06/07.
  Registrar as variáveis `WATSONX_*` e `LOCAL_LLM_*` (sem valores) em `.env.example`.
- **P4 (detecção):** o detector CUSUM com regiões já está na `main`. A explicação usa
  `region`, `suspected_onset`, `cusum` e `since` quando eles vêm preenchidos.
- **P1 (dados):** os IDs reais do BattLeDIM (`config/sensors.battledim.json`: `n1`, `n54`,
  `n105`…) estão na `main`; a validação os reconhece (padrão letra+número).
- **P2 (ML):** `contributions` (TreeSHAP) no `ForecastResult`; o backend já as restringe aos
  sensores do alerta.
- **P6 (dashboard):** exibir `explanation.text`, `source`/`fallback_reason`, `audit.seq` e o
  resultado da verificação.

## Verificação (2026-10-17, Windows x86_64, Python 3.13.5, dependências do `uv.lock`)

| Comando | Resultado |
|---|---|
| **Depois do merge da `main` (2026-10-18)**, `uv sync --locked --group dashboard` (como a CI) | — |
| `ruff format --check` + `ruff check` (`src tests scripts dashboard`) | ok, 140 arquivos |
| `mypy` | sem problemas em 140 arquivos |
| `pytest` (suíte inteira) | **757 passed, 20 skipped, 3 failed**. As 3 falhas são de `tests/test_eval_run.py` (P6) e só acontecem no Windows: `write_text` sem `encoding="utf-8"` e um caminho comparado com `/`. Nenhuma toca código da P5. |
| testes da P5 (`test_audit_chain`, `test_narrative`, `test_local_granite`, `test_watsonx`) | 107 passed |
| Explicações em segundo plano (2026-10-18): `pytest` suíte inteira | **770 passed**, 20 skipped, 3 failed (as mesmas 3 do P6, só no Windows); P5: 120 passed |
| Ponta a ponta com `uvicorn` real (`devserver`, `SENTINEL_NARRATOR=none`, IDs oficiais do BattLeDIM), 288 janelas | 288 × 201; 1 alerta; `GET .../explanation` `ready` em en e pt; cadeia de auditoria ok (289 registros) e de explicações ok (2) |
| Explicações com o Granite no processo da API na VM (`SENTINEL_NARRATOR=llamacpp`) | rodado pelo P5; `verify_explanation` do último registro contra o `alert.raised`: **"explicação ligada ao alerta: OK"** (JSON da explicação não registrado aqui) |
| **2º merge da `main` (2026-10-18)**: alerta 1.1 (`candidate_pipe`), regiões do L-Town | merge sem conflito; P5: 125 passed (com o exemplo oficial `alert.cusum-synthetic.json`); formatação e lint ok; suíte inteira **849 passed, 34 skipped, 4 failed**. As 4 falhas só acontecem no Windows e estão fora da P5: as 3 de `test_eval_run.py` (P6) e `test_topology_files.py::test_the_committed_regions_file_is_the_one_that_was_fixed` (P4: hash de `config/regions.battledim.json`, que o Git do Windows grava com CRLF, 428 bytes em vez de 415). |
| Antes do merge: `pytest` (suíte inteira) | 194 passed (89 do backend + 105 da P5) |
| `p5_linuxone_granite.ipynb` executado (Windows) contra um servidor de completions falso | 0 erros; texto aceito no alerta inventado, exemplo copiado recusado no alerta do pipeline |
| `python -m sentinel.narrative --local` contra o mesmo servidor falso (HTTP real) | `source: llm` |
| `p5_linuxone_granite.ipynb` (versão `llama_cpp` no kernel) executado no Windows com um `llama_cpp` falso | 0 erros; carrega, aceita o texto válido, recusa o exemplo copiado (en/pt), descarrega |
| Granite GGUF real na VM, `p5_linuxone_granite.ipynb` | carregou (11,1 s); 3 explicações em 34–44 s; ver "Resultado na VM" acima |
| Granite na VM, 2ª rodada (prompt enxuto + regra de números no lugar errado) | 3 recusas em 26,9–34,1 s; ver "2ª rodada" acima |
| Granite na VM, 3ª rodada (aviso pelo código + exemplos do mesmo formato) | **3/3 aceitos e corretos** em 26,0–33,6 s; ver "3ª rodada" acima |
| Granite na VM depois do merge da `main` (z positivo no prompt, Q-DET-02) | 3/3 aceitos e corretos em 25,3–33,4 s; **textos e tokens idênticos aos da 3ª rodada** (677+87, 548+68, 586+79): com `temperature=0` o resultado é reproduzível no s390x |
| Avaliação do Granite com muitos alertas variados (taxa de aceitação) | **NOT_RUN** |
| LinuxONE VM (s390x), branch `leo/local-version` @ `033959d`: `python -m pytest -q tests/test_audit_chain.py tests/test_narrative.py tests/test_local_granite.py tests/test_watsonx.py` | **93 passed** in 0.27 s |
| `jupyter nbconvert --execute` do notebook (kernel real, sem `WATSONX_*`) | 19 células, 0 erros; Granite pulado |
| Ponta a ponta local: `devserver` + `make_synthetic` + `replay` + `verify --alert` + `sentinel.narrative --alert` | 1 alerta mock; cadeia 289 registros ok; alerta ok; template |
| `foundation_model_specs` (público) por região | Granite de chat só em us-south e eu-de (`ibm/granite-4-h-small`) |
| LinuxONE VM (s390x): `p5_auditoria_narrativa.ipynb` completo no Jupyter da VM | todas as seções ok: cadeia de 289 registros válida, adulterações detectadas, alerta `A-20190314T1640Z-1` (mesmo ID e mesmos desvios do x86) ligado ao registro seq 200, LLM falso aceito/recusado como esperado |
| Paridade x86 × s390x do JSON canônico | `payload_sha256` de `{"i": 1, "status": "scored"}` = `993ee9cc…20d79f` nas duas plataformas |
| API ponta a ponta na VM, dados reais, watsonx.ai com credenciais | **NOT_RUN** |
