"""Gera config/l-town-map.json, o desenho enxuto da rede L-Town para o mapa do dashboard (P1).

Uso: python -m sentinel.data.export_map --version v1 [--force]

Lê $SENTINEL_DATA_DIR/network/<versão>/l-town.json (gerado por build_network) e
config/regions.battledim.json, e guarda só o que um mapa precisa: o segmento de cada ligação
(cano, bomba, válvula), o sensor de pressão mais próximo e a região de cada cano, os 33 sensores
de pressão e os tanques/reservatórios. O arquivo é pequeno e vai para o git, então o repositório
público consegue desenhar o mapa sem os dados da VM. Fonte: BattLeDIM (CC BY 4.0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
ATRIBUICAO = (
    "L-TOWN.inp from the BattLeDIM 2020 dataset (Vrachimis et al.), CC BY 4.0, "
    "https://doi.org/10.5281/zenodo.4017659"
)
CASAS = 2  # coordenadas em metros; 1 cm basta para desenhar


def _r(v: float) -> float:
    return round(float(v), CASAS)


def exportar_mapa(
    rede: dict[str, Any], regioes: dict[str, list[str]], regioes_sha256: str
) -> dict[str, Any]:
    """Desenho do mapa a partir da rede de build_network e do mapeamento de regiões."""
    nos = {n["id"]: n for n in rede["nodes"]}
    pressao = list(rede["sensors"]["pressure"])
    regiao_do_sensor: dict[str, str] = {}
    for regiao, membros in regioes.items():
        for s in membros:
            if s in regiao_do_sensor:
                raise ValueError(f"sensor {s} em duas regiões")
            regiao_do_sensor[s] = regiao
    faltando = sorted(set(pressao) - set(regiao_do_sensor))
    if faltando:
        raise ValueError(f"sensores de pressão sem região: {faltando}")

    ligacoes = []
    for lig in rede["links"]:
        a, b = nos[lig["from"]], nos[lig["to"]]
        sensor = lig.get("nearest_pressure_sensor")
        ligacoes.append(
            {
                "id": lig["id"],
                "type": lig["type"],
                "x1": _r(a["x"]),
                "y1": _r(a["y"]),
                "x2": _r(b["x"]),
                "y2": _r(b["y"]),
                "diameter": lig.get("diameter"),
                "sensor": sensor,
                "region": regiao_do_sensor.get(sensor) if sensor else None,
            }
        )

    sensores = [
        {"id": s, "x": _r(nos[s]["x"]), "y": _r(nos[s]["y"]), "region": regiao_do_sensor[s]}
        for s in pressao
    ]
    marcos = [
        {"id": n["id"], "type": n["type"], "x": _r(n["x"]), "y": _r(n["y"])}
        for n in rede["nodes"]
        if n["type"] in ("tank", "reservoir")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "network": "L-Town (BattLeDIM 2020)",
        "attribution": ATRIBUICAO,
        "source": {
            "network_sha256": rede["source"]["sha256"],
            "regions_sha256": regioes_sha256,
        },
        "counts": {
            "nodes": len(rede["nodes"]),
            "pipes": sum(1 for lig in rede["links"] if lig["type"] == "pipe"),
            "pressure_sensors": len(pressao),
        },
        "bbox": [_r(v) for v in rede["bbox"]],
        "links": ligacoes,
        "sensors": sensores,
        "landmarks": marcos,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    ap.add_argument("--version", default="v1")
    ap.add_argument("--regions", type=Path, default=Path("config/regions.battledim.json"))
    ap.add_argument("--out", type=Path, default=Path("config/l-town-map.json"))
    ap.add_argument("--force", action="store_true", help="sobrescreve --out se já existir")
    args = ap.parse_args(argv)
    if not args.data_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --data-dir.")
    if args.out.exists() and not args.force:
        raise SystemExit(f"{args.out} já existe; use --force para gerar de novo.")

    entrada = Path(args.data_dir).expanduser() / "network" / args.version / "l-town.json"
    rede = json.loads(entrada.read_text(encoding="utf-8"))
    bruto = args.regions.read_bytes()
    regioes = json.loads(bruto)["regions"]
    mapa = exportar_mapa(rede, regioes, hashlib.sha256(bruto).hexdigest())

    args.out.write_text(
        json.dumps(mapa, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    kb = args.out.stat().st_size / 1024
    c = mapa["counts"]
    print(
        f"{args.out} ({kb:.0f} KB): {len(mapa['links'])} ligações, {c['pipes']} canos, "
        f"{len(mapa['sensors'])} sensores de pressão, "
        f"{len(mapa['landmarks'])} tanques/reservatórios"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
