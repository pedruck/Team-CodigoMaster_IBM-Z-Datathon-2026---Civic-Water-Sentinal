"""Gera o gabarito de 2019 a partir da configuração oficial do BattLeDIM (P1, Q-DATA-07).

Uso: python -m sentinel.eval.build_labels --version v2

Fica em sentinel.eval porque é o único pacote que pode ler o gabarito de 2019
(tests/test_eval_run.py). Lê <eval>/raw/dataset_configuration.yaml (Zenodo do BattLeDIM), que
descreve a simulação inteira, com os vazamentos de 2018 e de 2019 juntos, e escreve
<eval>/<versão>/leaks_2019.csv só com os vazamentos ativos em 2019 (fim a partir de
2019-01-01): pipe, start, end, leak_size, type, peak. Os 4 herdados de 2018 (p257, p427, p654,
p810) mantêm o início e o pico de 2018, como na lista oficial. Antes de gravar, o arquivo é
conferido com check_official_labels: se não bater, nada é gravado. Uma versão nunca é
sobrescrita. <eval> é a pasta irmã de $SENTINEL_DATA_DIR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from sentinel.eval.labels import check_official_labels, read_labels

DATA = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"
LINHA = re.compile(
    rf"^\s*-\s*(p\d+)\s*,\s*{DATA}\s*,\s*{DATA}\s*,\s*([\d.eE+-]+)\s*,"
    rf"\s*(abrupt|incipient)\s*,\s*{DATA}\s*$"
)
COLUNAS = ["pipe", "start", "end", "leak_size", "type", "peak"]
INICIO_2019 = pd.Timestamp("2019-01-01")


def ler_vazamentos_yaml(texto: str) -> pd.DataFrame:
    """Todas as linhas '- pipe, início, fim, diâmetro, tipo, pico' do YAML; ignora o resto."""
    linhas: list[dict[str, Any]] = []
    for linha in texto.splitlines():
        m = LINHA.match(linha)
        if m:
            pipe, start, end, size, kind, peak = m.groups()
            linhas.append(
                {
                    "pipe": pipe,
                    "start": start,
                    "end": end,
                    "leak_size": float(size),
                    "type": kind,
                    "peak": peak,
                }
            )
    if not linhas:
        raise ValueError("nenhum vazamento encontrado no YAML")
    df = pd.DataFrame(linhas, columns=COLUNAS)
    for coluna in ("start", "end", "peak"):
        df[coluna] = pd.to_datetime(df[coluna])
    return df.sort_values(["start", "pipe"]).reset_index(drop=True)


def ativos_em_2019(df: pd.DataFrame) -> pd.DataFrame:
    """Vazamentos que ainda existem em 2019: os novos e os herdados de 2018."""
    return df[df["end"] >= INICIO_2019].reset_index(drop=True)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    dados = os.environ.get("SENTINEL_DATA_DIR")
    padrao = str(Path(dados).expanduser().parent / "eval") if dados else None
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-dir", default=padrao)
    ap.add_argument("--version", default="v2")
    args = ap.parse_args(argv)
    if not args.eval_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --eval-dir.")

    base = Path(args.eval_dir).expanduser()
    fonte = base / "raw" / "dataset_configuration.yaml"
    pasta = base / args.version
    saida = pasta / "leaks_2019.csv"
    if saida.exists():
        raise SystemExit(f"{saida} já existe. Uma versão nunca é sobrescrita; crie uma nova.")

    df = ativos_em_2019(ler_vazamentos_yaml(fonte.read_text(encoding="utf-8")))
    pasta.mkdir(parents=True, exist_ok=True)
    temporario = pasta / "leaks_2019.csv.tmp"
    df.to_csv(temporario, index=False)
    try:
        check_official_labels(read_labels(temporario))
    except ValueError:
        temporario.unlink()
        raise
    temporario.rename(saida)

    herdados = df.loc[df["start"] < INICIO_2019, "pipe"].tolist()
    manifest = {
        "version": args.version,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {"file": fonte.name, "sha256": _sha256(fonte)},
        "rows": len(df),
        "inherited_from_2018": herdados,
        "note": (
            "Vazamentos ativos em 2019. Herdados mantêm início e pico de 2018 (lista oficial, "
            "Q-DATA-07). Conferido com sentinel.eval.labels.check_official_labels."
        ),
        "files": {saida.name: {"bytes": saida.stat().st_size, "sha256": _sha256(saida)}},
    }
    (pasta / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"{saida} | {len(df)} vazamentos | herdados de 2018: {herdados}")
    print("Conferido com a lista oficial: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
