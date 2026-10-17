# Fluxo de trabalho do time

## Quem é dono do quê

| Área | Pasta | Dono | Estado herdado do backend |
|---|---|---|---|
| Dados e replay | `src/sentinel/data/`, `config/` | P1 | Gerador sintético e replay sintético prontos; IDs de sensores provisórios (Q-DATA-06) |
| Features e modelos | `src/sentinel/forecasting/` | P2 | Interface + mock; histórico de 288 janelas (mín. 144) |
| Backend | `src/sentinel/api/`, `services/`, `observability/` | P3 | API, validação, pipeline, métricas, CI (89 testes) |
| Detecção | `src/sentinel/detection/` | P4 | Interface + mock |
| Auditoria | `src/sentinel/audit/` | P5 | Interface + sinks de desenvolvimento com hash encadeado |
| Dashboard e avaliação | `dashboard/`, `src/sentinel/eval/` | P6 | Endpoints `GET /v1/alerts` e `/v1/status` prontos para consumo |

Contratos compartilhados: `src/sentinel/schemas/` e `docs/integration-contracts.md`.
Mudanças neles precisam de revisão dos donos afetados.

## Ciclo de desenvolvimento

1. No notebook, importe de `sentinel` o que é dos outros (real ou mock).
2. Prototipe nas células.
3. Funcionou: mova para `src/sentinel/<sua área>/`, implementando a interface existente.
4. O notebook passa a importar o seu módulo (com `%autoreload 2`).
5. Teste em `tests/`, branch própria, pull request para `main`.
6. A API troca o mock pelo real por configuração.

## Regras da VM (LinuxONE)

- Um só usuário Linux e um só ambiente Python para todos.
- Cada pessoa tem o próprio clone em `shared/civicwater/users/<nome>/repo`, com
  `git config user.name/user.email` **por repositório** (nunca `--global`).
- Não use `pip install -e .` em clones pessoais. Só na cópia de integração
  `shared/civicwater/main`, para a API usar o Python de `/opt/venv` (onde está o Snap ML).
- Não salve tokens do GitHub na VM.
- Desligue kernels parados (aba Running). Treino pesado vai no Colab.
- Dados (`shared/civicwater/data/vN/`) e modelos (`shared/civicwater/models/`) ficam fora do Git
  e são versionados pelo nome.

## Subir a API na VM

```bash
cd ~/shared/civicwater/main
uvicorn sentinel.main:create_app --factory --host 0.0.0.0 --port 8000
```

## Decisões em aberto com proposta

- **Q-DET-02 (sinal do z):** CUSUM usa z = (esperado − observado)/σ (queda = positivo).
  O payload reporta `deviation_m = observado − esperado` (negativo) e o mesmo z positivo.
- **Q-AUD-05 (circularidade):** `payload_sha256` é calculado sobre o alerta sem `audit_seq`.
- **Q-AUD-03 (fail-open):** usar fail-closed; alerta não gravado no log fica pendente
  e a falha aparece em `/v1/status`.
