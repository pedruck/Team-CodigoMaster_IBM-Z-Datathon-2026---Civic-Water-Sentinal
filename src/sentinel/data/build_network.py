"""Converte a rede L-TOWN (EPANET .inp) em JSON para o dashboard e as regiões (P1).

Uso: python -m sentinel.data.build_network

Lê $SENTINEL_DATA_DIR/raw/L-TOWN.inp (modelo nominal; nunca o L-TOWN_Real.inp) e escreve
$SENTINEL_DATA_DIR/network/<versão>/l-town.json com:
- nós (junções, reservatórios, tanque) com coordenadas em metros do plano do modelo,
  que não são latitude/longitude;
- ligações (canos, bomba, válvulas) com nó de origem e de destino;
- zonas hidráulicas: partes da rede ligadas só por canos abertos. Bomba e válvulas
  redutoras de pressão separam zonas;
- área de influência de cada sensor de pressão: cada nó e cada cano fica com o sensor
  mais próximo, medindo a distância pelos canos (Dijkstra com várias origens), sem
  atravessar bomba ou válvula.
A área de influência é uma aproximação para visualização, não a localização do vazamento.
Uma versão nunca é sobrescrita.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sentinel.sensors import SensorRegistry

Secoes = dict[str, list[list[str]]]
Adjacencia = dict[str, list[tuple[str, float]]]
Perto = dict[str, tuple[str, float]]

UNIDADES_SI = {"LPS", "LPM", "MLD", "CMH", "CMD"}


def ler_inp(path: Path) -> Secoes:
    """Lê as seções de um .inp: {"PIPES": [["p1", "n62", ...], ...], ...}."""
    secoes: Secoes = {}
    atual: str | None = None
    for bruta in path.read_text(encoding="utf-8", errors="replace").splitlines():
        linha = bruta.split(";", 1)[0].strip()
        if not linha:
            continue
        if linha.startswith("["):
            atual = linha.strip("[]").upper()
            secoes.setdefault(atual, [])
        elif atual is not None:
            secoes[atual].append(linha.split())
    return secoes


def _nos(secoes: Secoes) -> dict[str, dict[str, Any]]:
    coords = {c[0]: (float(c[1]), float(c[2])) for c in secoes.get("COORDINATES", [])}
    nos: dict[str, dict[str, Any]] = {}
    tipos = (("junction", "JUNCTIONS"), ("reservoir", "RESERVOIRS"), ("tank", "TANKS"))
    for tipo, secao in tipos:
        for c in secoes.get(secao, []):
            if c[0] not in coords:
                raise ValueError(f"nó sem coordenada: {c[0]}")
            x, y = coords[c[0]]
            nos[c[0]] = {"id": c[0], "type": tipo, "x": x, "y": y, "elevation": float(c[1])}
    return nos


def _ligacoes(secoes: Secoes) -> dict[str, dict[str, Any]]:
    ligacoes: dict[str, dict[str, Any]] = {}
    for c in secoes.get("PIPES", []):
        ligacoes[c[0]] = {
            "id": c[0],
            "type": "pipe",
            "from": c[1],
            "to": c[2],
            "length": float(c[3]),
            "diameter": float(c[4]),
            "status": c[7] if len(c) > 7 else "Open",
        }
    for c in secoes.get("PUMPS", []):
        ligacoes[c[0]] = {"id": c[0], "type": "pump", "from": c[1], "to": c[2]}
    for c in secoes.get("VALVES", []):
        ligacoes[c[0]] = {
            "id": c[0],
            "type": "valve",
            "from": c[1],
            "to": c[2],
            "diameter": float(c[3]),
            "valve_type": c[4],
        }
    return ligacoes


def _aberto(lig: dict[str, Any]) -> bool:
    return lig["type"] == "pipe" and str(lig["status"]).lower() != "closed"


def _adjacencia(ligacoes: dict[str, dict[str, Any]]) -> Adjacencia:
    adj: Adjacencia = {}
    for lig in ligacoes.values():
        if _aberto(lig):
            adj.setdefault(lig["from"], []).append((lig["to"], lig["length"]))
            adj.setdefault(lig["to"], []).append((lig["from"], lig["length"]))
    return adj


def _zonas(ids: list[str], adj: Adjacencia) -> dict[str, str]:
    """Componentes ligados só por canos abertos; Z1 é a maior."""
    componentes: list[list[str]] = []
    visto: set[str] = set()
    for inicio in ids:
        if inicio in visto:
            continue
        visto.add(inicio)
        pilha = [inicio]
        comp: list[str] = []
        while pilha:
            no = pilha.pop()
            comp.append(no)
            for vizinho, _ in adj.get(no, []):
                if vizinho not in visto:
                    visto.add(vizinho)
                    pilha.append(vizinho)
        componentes.append(comp)
    componentes.sort(key=len, reverse=True)
    return {no: f"Z{i}" for i, comp in enumerate(componentes, start=1) for no in comp}


def _mais_proximo(fontes: Sequence[str], adj: Adjacencia) -> Perto:
    """Dijkstra com várias origens: para cada nó, (sensor mais próximo, distância em m)."""
    melhor: Perto = {}
    fila: list[tuple[float, str, str]] = [(0.0, s, s) for s in fontes]
    heapq.heapify(fila)
    while fila:
        dist, no, origem = heapq.heappop(fila)
        if no in melhor:
            continue
        melhor[no] = (origem, dist)
        for vizinho, peso in adj.get(no, []):
            if vizinho not in melhor:
                heapq.heappush(fila, (dist + peso, vizinho, origem))
    return melhor


def _ligacao_mais_proxima(lig: dict[str, Any], perto: Perto) -> tuple[str | None, float | None]:
    """Um cano fica com o sensor mais próximo do seu ponto médio."""
    if not _aberto(lig):
        return None, None
    meio = float(lig["length"]) / 2
    opcoes = [(perto[n][1] + meio, perto[n][0]) for n in (lig["from"], lig["to"]) if n in perto]
    if not opcoes:
        return None, None
    dist, origem = min(opcoes)
    return origem, round(dist, 2)


def construir_rede(
    inp: Path, pressao: Sequence[str], vazao: Sequence[str], nivel: Sequence[str]
) -> dict[str, Any]:
    secoes = ler_inp(inp)
    opcoes = {c[0].lower(): c[1] for c in secoes.get("OPTIONS", []) if len(c) >= 2}
    unidade = opcoes.get("units", "GPM").upper()
    if unidade not in UNIDADES_SI:
        raise ValueError(f"o .inp não está em unidades SI: {unidade}")

    nos = _nos(secoes)
    ligacoes = _ligacoes(secoes)
    faltando = [s for s in [*pressao, *nivel] if s not in nos]
    faltando += [s for s in vazao if s not in ligacoes]
    if faltando:
        raise ValueError(f"sensores ausentes na rede: {faltando}")

    adj = _adjacencia(ligacoes)
    zona = _zonas(list(nos), adj)
    perto = _mais_proximo(pressao, adj)
    tipo_sensor = {s: "pressure" for s in pressao}
    tipo_sensor.update({s: "tank_level" for s in nivel})
    tipo_sensor.update({s: "flow" for s in vazao})
    influencia: dict[str, dict[str, list[str]]] = {s: {"nodes": [], "links": []} for s in pressao}

    for no in nos.values():
        achado = perto.get(no["id"])
        no["sensor"] = tipo_sensor.get(no["id"])
        no["zone"] = zona[no["id"]]
        no["nearest_pressure_sensor"] = achado[0] if achado else None
        no["distance_m"] = round(achado[1], 2) if achado else None
        if achado:
            influencia[achado[0]]["nodes"].append(no["id"])

    for lig in ligacoes.values():
        origem, dist = _ligacao_mais_proxima(lig, perto)
        lig["sensor"] = tipo_sensor.get(lig["id"])
        lig["zone"] = zona[lig["from"]] if _aberto(lig) else None
        lig["nearest_pressure_sensor"] = origem
        lig["distance_m"] = dist
        if origem is not None:
            influencia[origem]["links"].append(lig["id"])

    resumo: dict[str, dict[str, Any]] = {}
    for no in nos.values():
        z = resumo.setdefault(no["zone"], {"nodes": 0, "pressure_sensors": []})
        z["nodes"] += 1
        if no["sensor"] == "pressure":
            z["pressure_sensors"].append(no["id"])

    xs = [n["x"] for n in nos.values()]
    ys = [n["y"] for n in nos.values()]
    return {
        "schema_version": "1.0",
        "network": "L-TOWN",
        "source": {"file": inp.name, "sha256": hashlib.sha256(inp.read_bytes()).hexdigest()},
        "units": {
            "coordinates": "m (plano do modelo, não lat/lon)",
            "length": "m",
            "diameter": "mm",
            "elevation": "m",
            "flow": unidade,
        },
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "nodes": list(nos.values()),
        "links": list(ligacoes.values()),
        "sensors": {"pressure": list(pressao), "flow": list(vazao), "tank_level": list(nivel)},
        "zones": dict(sorted(resumo.items(), key=lambda kv: int(kv[0][1:]))),
        "influence": influencia,
    }


def _imprimir_resumo(rede: dict[str, Any], saida: Path) -> None:
    tipos: dict[str, int] = {}
    for lig in rede["links"]:
        tipos[lig["type"]] = tipos.get(lig["type"], 0) + 1
    sem = sum(1 for n in rede["nodes"] if n["nearest_pressure_sensor"] is None)
    distancias = [n["distance_m"] for n in rede["nodes"] if n["distance_m"] is not None]
    print(f"{saida} ({saida.stat().st_size // 1024} KB)")
    print(f"nós: {len(rede['nodes'])} | ligações: {tipos}")
    for zid, z in rede["zones"].items():
        print(f"zona {zid}: {z['nodes']} nós, {len(z['pressure_sensors'])} sensores de pressão")
    print(f"nós sem sensor alcançável: {sem} | maior distância até um sensor: {max(distancias)} m")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    ap.add_argument("--registry", type=Path, default=Path("config/sensors.battledim.json"))
    ap.add_argument("--version", default="v1")
    args = ap.parse_args(argv)
    if not args.data_dir:
        raise SystemExit("Defina SENTINEL_DATA_DIR ou passe --data-dir.")

    dados = Path(args.data_dir).expanduser()
    saida = dados / "network" / args.version / "l-town.json"
    if saida.exists():
        raise SystemExit(f"{saida} já existe. Uma versão nunca é sobrescrita; crie uma nova.")

    reg = SensorRegistry.load(args.registry)
    rede = construir_rede(dados / "raw" / "L-TOWN.inp", reg.pressure, reg.flow, reg.tank_level)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps(rede, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    _imprimir_resumo(rede, saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
