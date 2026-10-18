"""Testes do desenho enxuto da rede para o mapa (P1, sentinel.data.export_map).

Rede inventada (a mesma ideia de tests/test_network.py), com respostas conhecidas:
  n1 -p10- nA -p11- nB -p12- n105 =V1= nC -p13- nD ; p14 (fechado) liga n1 a nB.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sentinel.data.build_network import construir_rede
from sentinel.data.export_map import exportar_mapa

INP = """[JUNCTIONS]
 n1    10  0
 nA    10  0
 nB    10  0
 n105  10  0
 nC    10  0
 nD    10  0
[RESERVOIRS]
 R1    50
[PIPES]
 p10  n1   nA    50  100  120  0  Open
 p11  nA   nB    80  100  120  0  Open
 p12  nB   n105  40  100  120  0  Open
 p13  nC   nD    10  100  120  0  Open
 p14  n1   nB     1  100  120  0  Closed
 p15  R1   n1     5  300  120  0  Open
[VALVES]
 V1   n105 nC   100  PRV  40  0
[OPTIONS]
 Units  CMH
[COORDINATES]
 n1    0    0
 nA    50   0
 nB    130  0
 n105  170  0
 nC    180  0
 nD    190  0.004
 R1    0    -10
[END]
"""

RAIZ = Path(__file__).resolve().parents[1]
MAPA = RAIZ / "config" / "l-town-map.json"
REGIOES = RAIZ / "config" / "regions.battledim.json"


@pytest.fixture
def rede(tmp_path: Path) -> dict[str, Any]:
    inp = tmp_path / "mini.inp"
    inp.write_text(INP, encoding="utf-8")
    return construir_rede(inp, pressao=("n1", "n105"), vazao=("p10",), nivel=())


def _por_id(itens: list[dict[str, Any]], ident: str) -> dict[str, Any]:
    return next(i for i in itens if i["id"] == ident)


def test_segmentos_regioes_e_marcos(rede: dict[str, Any]) -> None:
    mapa = exportar_mapa(rede, {"RA": ["n1"], "RB": ["n105"]}, "abc")
    p11 = _por_id(mapa["links"], "p11")
    assert (p11["x1"], p11["y1"], p11["x2"], p11["y2"]) == (50.0, 0.0, 130.0, 0.0)
    assert p11["diameter"] == 100.0
    assert _por_id(mapa["links"], "p10")["region"] == "RA"
    assert _por_id(mapa["links"], "p12")["region"] == "RB"
    assert _por_id(mapa["links"], "p14")["type"] == "pipe"  # fechado, mas desenhado
    assert _por_id(mapa["links"], "V1")["type"] == "valve"
    assert _por_id(mapa["links"], "p13")["y2"] == 0.0  # 2 casas decimais
    assert [s["id"] for s in mapa["sensors"]] == ["n1", "n105"]
    assert _por_id(mapa["sensors"], "n105")["region"] == "RB"
    assert mapa["landmarks"] == [{"id": "R1", "type": "reservoir", "x": 0.0, "y": -10.0}]
    assert mapa["counts"] == {"nodes": 7, "pipes": 6, "pressure_sensors": 2}
    assert mapa["source"]["regions_sha256"] == "abc"
    assert "CC BY 4.0" in mapa["attribution"]


def test_sensor_sem_regiao_e_recusado(rede: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="sem região"):
        exportar_mapa(rede, {"RA": ["n1"]}, "abc")


def test_sensor_em_duas_regioes_e_recusado(rede: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="duas regiões"):
        exportar_mapa(rede, {"RA": ["n1", "n105"], "RB": ["n105"]}, "abc")


@pytest.mark.skipif(not MAPA.exists(), reason="config/l-town-map.json ainda não foi gerado")
def test_mapa_versionado_bate_com_a_rede_e_as_regioes() -> None:
    mapa = json.loads(MAPA.read_text(encoding="utf-8"))
    regioes = json.loads(REGIOES.read_text(encoding="utf-8"))["regions"]
    regiao_do_sensor = {s: r for r, membros in regioes.items() for s in membros}
    assert mapa["counts"] == {"nodes": 785, "pipes": 905, "pressure_sensors": 33}
    assert sum(1 for lk in mapa["links"] if lk["type"] == "pipe") == 905
    assert {s["id"]: s["region"] for s in mapa["sensors"]} == regiao_do_sensor
    assert {lk["region"] for lk in mapa["links"]} <= {*regioes, None}
    assert {m["id"] for m in mapa["landmarks"]} == {"R1", "R2", "T1"}
