"""Gera o MANIFEST.json de uma versão de dados (P1).

Uso: python -m sentinel.data.manifest --version v1

Lê $SENTINEL_DATA_DIR/<versão> e registra, para cada arquivo, tamanho, SHA-256 e,
para as séries SCADA, linhas, período e NaN por coluna. Também registra o SHA-256
dos arquivos brutos em $SENTINEL_DATA_DIR/raw, de onde a versão foi gerada.
Uma versão nunca é sobrescrita: se o MANIFEST já existir, o script para.
"""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def descrever(p: Path) -> dict:
    info = {"bytes": p.stat().st_size, "sha256": sha256(p)}
    if p.name.startswith("scada_"):
        df = pd.read_csv(p, index_col="timestamp", parse_dates=True)
        na = df.isna().sum()
        info.update(
            rows=len(df),
            columns=df.shape[1],
            start=str(df.index.min()),
            end=str(df.index.max()),
            nan_total=int(na.sum()),
            nan_by_column={c: int(n) for c, n in na.items() if n},
        )
    elif p.suffix == ".csv":
        info["rows"] = len(pd.read_csv(p))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    args = ap.parse_args(argv)
    if not args.data_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --data-dir.")

    base = Path(args.data_dir).expanduser()
    vdir = base / args.version
    saida = vdir / "MANIFEST.json"
    if saida.exists():
        raise SystemExit(f"{saida} já existe. Uma versão nunca é sobrescrita; crie uma nova.")

    raw = base / "raw"
    manifest = {
        "version": args.version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "BattLeDIM (L-Town), carregado com epyt-flow no Colab",
        "raw_files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
                      for p in sorted(raw.iterdir()) if p.is_file()} if raw.exists() else {},
        "timestamp": "fim da janela de 5 min; horário simulado, sem fuso",
        "units": {"pressure": "m", "flow": "m3/h", "level": "m"},
        "cleaning": {
            "dropped": "82 colunas Demand_*",
            "valid_ranges": {"Pressure_": [0, 100], "Flow_": [0, 500], "Level_": [0, 10]},
            "out_of_range": "vira NaN",
            "interpolate_max_gap_windows": 3,
        },
        "files": {p.name: descrever(p) for p in sorted(vdir.iterdir())
                  if p.is_file() and p.name != "MANIFEST.json"},
    }
    saida.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
