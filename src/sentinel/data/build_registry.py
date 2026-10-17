"""Gera config/sensors.battledim.json, o registro oficial de sensores (P1, DEP-DATA-01).

Uso: python -m sentinel.data.build_registry --version v1

Os IDs são os elementos da rede L-Town (nó n<N>, cano p<N>, bomba PUMP_1, tanque T1),
lidos de $SENTINEL_DATA_DIR/<versão>/sensors.csv, na mesma ordem das colunas do SCADA.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from sentinel.sensors import SensorRegistry

TIPOS = {"pressao": "pressure", "vazao": "flow", "nivel": "tank_level"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    ap.add_argument("--out", type=Path, default=Path("config/sensors.battledim.json"))
    args = ap.parse_args(argv)
    if not args.data_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --data-dir.")

    sensors = pd.read_csv(Path(args.data_dir).expanduser() / args.version / "sensors.csv")
    registro: dict[str, Any] = {
        "provisional": False,
        "note": (
            "IDs oficiais do BattLeDIM L-Town (elemento da rede), gerados de "
            f"data/{args.version}/sensors.csv. Unidades: pressão m, vazão m3/h, nível m."
        ),
    }
    for tipo, campo in TIPOS.items():
        registro[campo] = sensors.loc[sensors["tipo"] == tipo, "elemento_rede"].tolist()
    args.out.write_text(json.dumps(registro, indent=2, ensure_ascii=False) + "\n")

    reg = SensorRegistry.load(args.out)
    print(args.out, reg.counts, "provisional:", reg.provisional)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
