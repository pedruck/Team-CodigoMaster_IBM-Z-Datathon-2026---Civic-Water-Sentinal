"""Testes do conversor da rede L-TOWN (P1).

A rede pequena abaixo é inventada para ter respostas conhecidas:
  n1 -p10 (50 m)- nA -p11 (80 m)- nB -p12 (40 m)- n105 =V1 (válvula)= nC -p13- nD
  p14 liga n1 a nB com 1 m, mas está fechado: não pode encurtar o caminho.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sentinel.data.build_network import construir_rede
from sentinel.sensors import SensorRegistry

INP = """[JUNCTIONS]
 n1    10  0  ;sensor
 nA    10  0
 nB    10  0
 n105  10  0
 nC    10  0
 nD    10  0
[PIPES]
 p10  n1   nA    50  100  120  0  Open
 p11  nA   nB    80  100  120  0  Open
 p12  nB   n105  40  100  120  0  Open
 p13  nC   nD    10  100  120  0  Open
 p14  n1   nB     1  100  120  0  Closed
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
 nD    190  0
[END]
"""

REGISTRO = Path(__file__).resolve().parents[1] / "config" / "sensors.battledim.json"
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
INP_REAL = DATA_DIR / "raw" / "L-TOWN.inp"


@pytest.fixture
def rede(tmp_path: Path) -> dict[str, Any]:
    inp = tmp_path / "mini.inp"
    inp.write_text(INP, encoding="utf-8")
    return construir_rede(inp, pressao=("n1", "n105"), vazao=("p10",), nivel=())


def _por_id(itens: list[dict[str, Any]], ident: str) -> dict[str, Any]:
    return next(i for i in itens if i["id"] == ident)


def test_no_fica_com_o_sensor_mais_proximo_pelos_canos(rede: dict[str, Any]) -> None:
    assert _por_id(rede["nodes"], "nA")["nearest_pressure_sensor"] == "n1"
    assert _por_id(rede["nodes"], "nA")["distance_m"] == 50.0
    nb = _por_id(rede["nodes"], "nB")
    assert nb["nearest_pressure_sensor"] == "n105"
    assert nb["distance_m"] == 40.0


def test_cano_fechado_nao_encurta_caminho(rede: dict[str, Any]) -> None:
    assert _por_id(rede["nodes"], "nB")["nearest_pressure_sensor"] == "n105"
    assert _por_id(rede["links"], "p14")["nearest_pressure_sensor"] is None


def test_cano_fica_com_o_sensor_mais_proximo_do_meio(rede: dict[str, Any]) -> None:
    p11 = _por_id(rede["links"], "p11")
    assert p11["nearest_pressure_sensor"] == "n105"
    assert p11["distance_m"] == 80.0


def test_valvula_separa_zonas(rede: dict[str, Any]) -> None:
    assert list(rede["zones"]) == ["Z1", "Z2"]
    assert rede["zones"]["Z1"]["pressure_sensors"] == ["n1", "n105"]
    assert rede["zones"]["Z2"]["pressure_sensors"] == []
    assert _por_id(rede["nodes"], "nC")["nearest_pressure_sensor"] is None
    assert _por_id(rede["links"], "V1")["zone"] is None


def test_influencia_e_sensores_marcados(rede: dict[str, Any]) -> None:
    assert rede["influence"]["n105"]["nodes"] == ["nB", "n105"]
    assert "p12" in rede["influence"]["n105"]["links"]
    assert _por_id(rede["links"], "p10")["sensor"] == "flow"
    assert _por_id(rede["nodes"], "n1")["sensor"] == "pressure"


def test_sensor_ausente_na_rede_falha(tmp_path: Path) -> None:
    inp = tmp_path / "mini.inp"
    inp.write_text(INP, encoding="utf-8")
    with pytest.raises(ValueError):
        construir_rede(inp, pressao=("n999",), vazao=(), nivel=())


@pytest.mark.skipif(not INP_REAL.exists(), reason="L-TOWN.inp indisponível")
def test_rede_real_tem_todos_os_sensores() -> None:
    reg = SensorRegistry.load(REGISTRO)
    rede = construir_rede(INP_REAL, reg.pressure, reg.flow, reg.tank_level)
    assert len(rede["nodes"]) == 785
    assert sum(1 for n in rede["nodes"] if n["sensor"] == "pressure") == 33
    assert sum(1 for lig in rede["links"] if lig["type"] == "pipe") == 905
