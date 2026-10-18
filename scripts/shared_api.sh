#!/usr/bin/env bash
# API compartilhada (cópia de integração) na VM LinuxONE. Procedimento: docs/runbook-vm.md.
#
# Roda no HOST da VM (usuário linux1), não no terminal do Jupyter: o tmux do host mantém a
# sessão viva quando o SSH cai, e o `docker exec` executa a API no container, onde está o
# Python com as dependências (/opt/venv).
#
#   scripts/shared_api.sh start | stop | restart | status | logs
#
# Variáveis (todas opcionais): PORT (8000), CONTAINER (notebook), APP_DIR (cópia de
# integração, caminho dentro do container), SENTINEL_SENSOR_REGISTRY, SENTINEL_AUDIT_SINK,
# SENTINEL_LOG_LEVEL, os modelos (SENTINEL_FORECASTER*, SENTINEL_DETECTOR*; ver MODEL_VARS) e a
# cadeia de auditoria com as explicações (SENTINEL_AUDIT_RUN/DIR, SENTINEL_NARRATOR, LOCAL_LLM_*).
#
# Padrões de segurança da instância compartilhada:
# - auditoria em cadeia (SENTINEL_AUDIT_SINK=chain); sem SENTINEL_AUDIT_RUN, cada start abre uma
#   cadeia nova shared-<porta>-<hora UTC>; `memory` só se pedido explicitamente;
# - POST exige token: gerado uma vez em $HOME/shared/civicwater/secrets/ingest-<porta>.token
#   (modo 600) e passado como arquivo, nunca na linha de comando. O valor nunca é impresso.
set -euo pipefail

PORT="${PORT:-8000}"
CONTAINER="${CONTAINER:-notebook}"
APP_DIR="${APP_DIR:-/home/ibm-user/shared/civicwater/main}"
LOG_DIR="${LOG_DIR:-$HOME/shared/civicwater/logs}"   # mesmo diretório, visto do host
SESSION="sentinel-api-${PORT}"
REGISTRY="${SENTINEL_SENSOR_REGISTRY:-config/sensors.battledim.json}"
AUDIT="${SENTINEL_AUDIT_SINK:-chain}"
CONTAINER_SHARED="$(dirname "$APP_DIR")"              # ~/shared/civicwater visto do container
TOKEN_NAME="ingest-${PORT}.token"
TOKEN_FILE="$HOME/shared/civicwater/secrets/$TOKEN_NAME"   # visto do host
LOG_LEVEL="${SENTINEL_LOG_LEVEL:-INFO}"
# Repassadas ao container só quando definidas (caminho vazio é erro de configuração).
MODEL_VARS=(SENTINEL_FORECASTER SENTINEL_FORECASTER_PATH SENTINEL_FORECASTER_CONTRIBUTIONS
    SENTINEL_FORECASTER_ENGINE SENTINEL_FORECASTER_TREE_FORMAT SENTINEL_FORECASTER_SHA256
    SENTINEL_MAX_GAP_SECONDS SENTINEL_FUTURE_TOLERANCE_SECONDS SENTINEL_MAX_BODY_BYTES
    SENTINEL_DETECTOR SENTINEL_DETECTOR_CALIBRATION SENTINEL_DETECTOR_REGIONS
    SENTINEL_DETECTOR_NETWORK
    SENTINEL_AUDIT_RUN SENTINEL_AUDIT_DIR SENTINEL_NARRATOR
    LOCAL_LLM_MODEL_PATH LOCAL_LLM_THREADS LOCAL_LLM_CTX LOCAL_LLM_URL LOCAL_LLM_MODEL
    LOCAL_LLM_TIMEOUT_S)
# WATSONX_* fica de fora de propósito: a chave apareceria na linha de comando do tmux/docker
# (visível em `ps` para todos na VM). Narrador watsonx só numa instância pessoal, via .env.
START_TIMEOUT="${START_TIMEOUT:-120}"   # o forecaster real carrega os artefatos antes de responder
PATTERN="uvicorn sentinel.main:create_app --factory --host 0.0.0.0 --port ${PORT}"

die() { echo "erro: $*" >&2; exit 1; }

in_container() { docker exec -w "$APP_DIR" "$CONTAINER" "$@"; }

# O container não tem pgrep/pkill: lista/sinaliza os processos da API lendo /proc em Python.
api_procs() {
    in_container python -c 'import os, signal, sys
action, pattern, me = sys.argv[1], sys.argv[2], os.getpid()
for pid in (int(p) for p in os.listdir("/proc") if p.isdigit()):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
    except OSError:
        continue
    if pid != me and pattern in cmd:
        print(pid)
        if action == "term":
            os.kill(pid, signal.SIGTERM)' "$1" "$PATTERN"
}

running() { [ -n "$(api_procs list)" ]; }

http_get() {
    in_container python -c "import sys,urllib.request
print(urllib.request.urlopen('http://127.0.0.1:${PORT}' + sys.argv[1], timeout=5).read().decode())" "$1"
}

latest_log() {
    find "$LOG_DIR" -maxdepth 1 -name "api-${PORT}-*.log" -printf '%T@ %p\n' 2> /dev/null \
        | sort -n | tail -1 | cut -d' ' -f2-
}

ensure_token() {
    [ -s "$TOKEN_FILE" ] && return 0
    (umask 077 && mkdir -p "$(dirname "$TOKEN_FILE")" \
        && python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$TOKEN_FILE")
    echo "token de escrita criado em $TOKEN_FILE (modo 600)"
}

start() {
    command -v tmux > /dev/null || die "tmux não encontrado no host"
    running && die "já existe uma API na porta $PORT (use: $0 status | restart)"
    tmux has-session -t "$SESSION" 2> /dev/null && tmux kill-session -t "$SESSION"
    mkdir -p "$LOG_DIR"
    local log
    log="$LOG_DIR/api-${PORT}-$(date -u +%Y%m%dT%H%M%SZ).log"
    ensure_token
    if [ "$AUDIT" = chain ] && [ -z "${SENTINEL_AUDIT_RUN:-}" ]; then
        SENTINEL_AUDIT_RUN="shared-${PORT}-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    local model_env="" var
    for var in "${MODEL_VARS[@]}"; do
        if [ -n "${!var:-}" ]; then model_env+="-e $var='${!var}' "; fi
    done
    echo "commit: $(in_container git log --oneline -1)" | tee "$log"
    echo "modelos: ${model_env:-padrão (mocks)}" | tee -a "$log"
    tmux new-session -d -s "$SESSION" \
        "docker exec -w '$APP_DIR' \
            -e SENTINEL_SENSOR_REGISTRY='$REGISTRY' \
            -e SENTINEL_AUDIT_SINK='$AUDIT' \
            -e SENTINEL_LOG_LEVEL='$LOG_LEVEL' \
            -e SENTINEL_INGEST_TOKEN_FILE='$CONTAINER_SHARED/secrets/$TOKEN_NAME' \
            $model_env \
            -e PYTHONPATH='$APP_DIR/src' \
            -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
            '$CONTAINER' python -m $PATTERN --no-access-log 2>&1 | tee -a '$log'"
    for _ in $(seq 1 "$START_TIMEOUT"); do
        if http_get /health > /dev/null 2>&1; then
            echo "API no ar: porta $PORT, sessão tmux '$SESSION', log $log"
            return 0
        fi
        sleep 1
    done
    tail -n 30 "$log" >&2
    die "a API não respondeu em ${START_TIMEOUT} s (log acima)"
}

stop() {
    if running; then
        # Encerrar o tmux não mata o processo dentro do container; ele é parado aqui.
        api_procs term > /dev/null
        for _ in $(seq 1 10); do running || break; sleep 1; done
        running && die "o processo não parou com SIGTERM"
    fi
    tmux has-session -t "$SESSION" 2> /dev/null && tmux kill-session -t "$SESSION"
    echo "API da porta $PORT parada (histórico, alertas e fila de auditoria em memória foram perdidos)"
}

status() {
    if ! running; then
        echo "parada (porta $PORT)"
        return 1
    fi
    echo "rodando: porta $PORT, sessão tmux '$SESSION', log $(latest_log)"
    echo "  POST exige token: --token-file $CONTAINER_SHARED/secrets/$TOKEN_NAME (no container)"
    http_get /v1/status | python3 -c "import json,sys
d = json.load(sys.stdin)
keep = ['version', 'uptime_s', 'last_timestamp', 'history', 'sensor_registry', 'audit_sink',
        'audit_pending_events', 'audit_pending_alerts', 'mocks_active',
        'throughput_windows_per_s', 'error_rate']
for k in keep:
    print(f'  {k}: {d[k]}')
print('  forecaster:', d['forecaster'], '| detector:', d['detector'])
print('  windows:', {k: v for k, v in d['counters'].items() if k.startswith('windows_')})"
    if [ "$(http_get /v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["audit_sink"])')" = hash-chain ]; then
        echo "  audit head: $(http_get /v1/audit/head)"
    fi
}

logs() {
    local log
    log="$(latest_log)"
    [ -n "$log" ] || die "nenhum log em $LOG_DIR"
    exec tail -n 50 -f "$log"
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    logs) logs ;;
    *) echo "uso: $0 start|stop|restart|status|logs" >&2; exit 2 ;;
esac
