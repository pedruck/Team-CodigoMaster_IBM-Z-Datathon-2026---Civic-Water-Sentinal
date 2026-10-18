"""Gera as janelas do replay (contrato sentinel.window v1.0) a partir do SCADA tratado (P1).

Uso:
    python -m sentinel.data.replay_windows --year 2019
    python -m sentinel.data.replay_windows --year 2019 --start 2019-03-01 --end 2019-03-07

Lê $SENTINEL_DATA_DIR/<versão>/scada_<ano>.csv.gz e escreve uma janela por linha (JSONL)
em $SENTINEL_DATA_DIR/replay/<versão>/, pronta para scripts/replay.py.
Os IDs vêm do registro oficial de sensores. Nenhum rótulo de vazamento é lido ou escrito.
O BattLeDIM não tem fuso: o horário simulado é tratado como UTC. O timestamp marca o fim
da janela de 5 min.

Integridade: antes de ler, o SHA-256 do scada_<ano>.csv.gz é comparado com o MANIFEST.json da
versão (o script para se divergir ou faltar). Ao lado da saída fica <saída>.manifest.json com o
SHA-256 e a contagem do JSONL, o hash da fonte, do MANIFEST e do registro; scripts/replay.py
confere esse arquivo antes de enviar, e python -m sentinel.audit.replay_check compara a cadeia
de auditoria com o JSONL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from sentinel.data.manifest import sha256
from sentinel.schemas.windows import SensorWindow
from sentinel.sensors import SensorRegistry

PREFIXO = {"pressures": "Pressure_", "flows": "Flow_", "tank_levels": "Level_"}
SIDECAR_VERSION = "1.0"


def conferir_fonte(vdir: Path, nome: str) -> dict[str, Any]:
    """Compara o SHA-256 do arquivo com o MANIFEST.json da versão; devolve a entrada."""
    manifest_path = vdir / "MANIFEST.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} não existe: a versão de dados não está selada.")
    entrada = json.loads(manifest_path.read_text(encoding="utf-8"))["files"].get(nome)
    if entrada is None:
        raise SystemExit(f"{nome} não está no {manifest_path}.")
    atual = sha256(vdir / nome)
    if atual != entrada["sha256"]:
        raise SystemExit(
            f"{vdir / nome} difere do MANIFEST: sha256 {atual}, esperado {entrada['sha256']}."
        )
    return {"file": nome, "sha256": atual, "manifest_sha256": sha256(manifest_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2019)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    ap.add_argument("--registry", type=Path, default=Path("config/sensors.battledim.json"))
    ap.add_argument("--start", help="início (inclusive), ex.: 2019-03-01")
    ap.add_argument("--end", help="fim (inclusive), ex.: 2019-03-07")
    ap.add_argument("--source", default=None)
    args = ap.parse_args(argv)
    if not args.data_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --data-dir.")

    base = Path(args.data_dir).expanduser()
    reg = SensorRegistry.load(args.registry)
    if reg.provisional:
        raise SystemExit(f"{args.registry} é provisório; use o registro oficial.")

    fonte = conferir_fonte(base / args.version, f"scada_{args.year}.csv.gz")
    df = pd.read_csv(
        base / args.version / fonte["file"],
        index_col="timestamp",
        parse_dates=True,
    )
    df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
    if args.start:
        df = df.loc[pd.Timestamp(args.start, tz="UTC") :]
    if args.end:
        df = df.loc[: pd.Timestamp(args.end, tz="UTC") + pd.Timedelta("23h55min")]
    idx = pd.DatetimeIndex(df.index)

    grupos: dict[str, tuple[str, ...]] = {
        "pressures": reg.pressure,
        "flows": reg.flow,
        "tank_levels": reg.tank_level,
    }
    colunas = {campo: [PREFIXO[campo] + sid for sid in ids] for campo, ids in grupos.items()}
    faltando = [c for cs in colunas.values() for c in cs if c not in df.columns]
    if faltando:
        raise SystemExit(f"Colunas do registro ausentes no SCADA: {faltando}")
    todas = [c for cs in colunas.values() for c in cs]
    if df[todas].isna().any().any():
        raise SystemExit("Há NaN no trecho escolhido; o contrato não aceita valores nulos.")

    source = args.source or f"replay-{args.year}-{args.version}"
    pasta = base / "replay" / args.version
    pasta.mkdir(parents=True, exist_ok=True)
    sufixo = f"_{args.start}_{args.end}" if (args.start or args.end) else ""
    saida = pasta / f"windows_{args.year}{sufixo}.jsonl"

    valores = {campo: df[cols].to_numpy() for campo, cols in colunas.items()}
    digest = hashlib.sha256()
    with saida.open("wb") as f:
        for i, ts in enumerate(idx):
            leituras = {
                campo: dict(zip(grupos[campo], map(float, valores[campo][i]), strict=True))
                for campo in grupos
            }
            janela = {
                "schema_version": "1.0",
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                **leituras,
                "source": source,
            }
            SensorWindow.model_validate(janela)
            linha = (json.dumps(janela, separators=(",", ":")) + "\n").encode("utf-8")
            digest.update(linha)
            f.write(linha)

    sidecar = {
        "sidecar_version": SIDECAR_VERSION,
        "file": saida.name,
        "sha256": digest.hexdigest(),
        "windows": len(df),
        "first": idx.min().strftime("%Y-%m-%dT%H:%M:%SZ") if len(df) else None,
        "last": idx.max().strftime("%Y-%m-%dT%H:%M:%SZ") if len(df) else None,
        "data_version": args.version,
        "source": fonte,
        "registry": {"path": str(args.registry), "sha256": sha256(args.registry)},
    }
    saida.with_name(saida.name + ".manifest.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )

    print(f"{saida} | {len(df)} janelas | {idx.min()} a {idx.max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
