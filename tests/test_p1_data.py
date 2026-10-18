"""Testes dos dados e utilitários da P1 (dados e replay).

Os testes que leem $SENTINEL_DATA_DIR são pulados quando os dados não existem
(por exemplo, no CI do GitHub). Os demais rodam em qualquer lugar.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from sentinel.data.clean_mask import mascara_limpa
from sentinel.sensors import SensorRegistry

REGISTRO = Path(__file__).resolve().parents[1] / "config" / "sensors.battledim.json"
DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
V1 = DATA_DIR / "v1"
precisa_dados = pytest.mark.skipif(
    not (V1 / "MANIFEST.json").exists(), reason="dados v1 indisponíveis"
)

PREFIXO = {"pressure": "Pressure_", "flow": "Flow_", "tank_level": "Level_"}
FAIXAS = {"Pressure_": (0, 100), "Flow_": (0, 500), "Level_": (0, 10)}
ANOS = [2018, 2019]
T = pd.Timestamp


# ---------- registro de sensores ----------


def test_registro_oficial() -> None:
    reg = SensorRegistry.load(REGISTRO)
    assert not reg.provisional
    assert reg.counts == {"pressure": 33, "flow": 3, "tank_level": 1}
    assert reg.units == {"pressure": "m", "flow": "m3/h", "tank_level": "m"}


# ---------- máscara de 2018 (vazamentos inventados) ----------

IDX = pd.date_range("2018-01-01", "2018-01-01 23:55", freq="5min")


def _vazamentos() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "pipe": ["pA", "pB"],
            "start": pd.to_datetime(["2018-01-01 01:00", "2018-01-01 03:00"]),
            "end": pd.to_datetime(["2018-01-01 02:00", "2018-01-01 23:55"]),
            "peak": pd.to_datetime(["2018-01-01 01:30", "2018-01-01 04:00"]),
            "repaired_in_2018": [True, False],
        }
    )


def test_mascara_reparados_exclui_so_o_reparado() -> None:
    m = mascara_limpa(IDX, _vazamentos(), "reparados")
    assert m[T("2018-01-01 00:55")]
    assert not m[T("2018-01-01 01:30")]
    assert m[T("2018-01-01 05:00")]


def test_mascara_crescimento_exclui_ate_o_pico() -> None:
    m = mascara_limpa(IDX, _vazamentos(), "reparados+crescimento")
    assert not m[T("2018-01-01 03:30")]
    assert m[T("2018-01-01 05:00")]


def test_mascara_todos_exclui_nao_reparado() -> None:
    m = mascara_limpa(IDX, _vazamentos(), "todos")
    assert not m[T("2018-01-01 05:00")]


def test_mascara_margem_apos_reparo() -> None:
    m = mascara_limpa(IDX, _vazamentos(), "reparados", margem_janelas=2)
    assert not m[T("2018-01-01 02:10")]
    assert m[T("2018-01-01 02:15")]


def test_mascara_politica_invalida() -> None:
    with pytest.raises(ValueError):
        mascara_limpa(IDX, _vazamentos(), "xyz")


# ---------- dados v1 na VM ----------


@functools.cache
def _scada(ano: int) -> pd.DataFrame:
    return pd.read_csv(V1 / f"scada_{ano}.csv.gz", index_col="timestamp", parse_dates=True)


@precisa_dados
@pytest.mark.parametrize("ano", ANOS)
def test_scada_grade_regular(ano: int) -> None:
    df = _scada(ano)
    assert len(df) == 105120
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()
    assert (df.index.to_series().diff().dropna() == pd.Timedelta("5min")).all()
    assert df.index.min() == T(f"{ano}-01-01 00:00")
    assert df.index.max() == T(f"{ano}-12-31 23:55")


@precisa_dados
@pytest.mark.parametrize("ano", ANOS)
def test_scada_colunas_do_registro(ano: int) -> None:
    df = _scada(ano)
    reg = SensorRegistry.load(REGISTRO)
    grupos = {"pressure": reg.pressure, "flow": reg.flow, "tank_level": reg.tank_level}
    esperadas = [PREFIXO[g] + s for g, ids in grupos.items() for s in ids]
    assert sorted(df.columns) == sorted(esperadas)


@precisa_dados
@pytest.mark.parametrize("ano", ANOS)
def test_scada_faixas_fisicas(ano: int) -> None:
    df = _scada(ano)
    for pref, (lo, hi) in FAIXAS.items():
        v = df.filter(like=pref)
        assert (((v >= lo) & (v <= hi)) | v.isna()).all().all(), pref


@precisa_dados
def test_manifesto_bate_com_arquivos() -> None:
    man = json.loads((V1 / "MANIFEST.json").read_text(encoding="utf-8"))
    for nome, info in man["files"].items():
        h = hashlib.sha256((V1 / nome).read_bytes()).hexdigest()
        assert h == info["sha256"], f"{nome} mudou desde a v1; gere uma v2"


@precisa_dados
def test_gabarito_2019_fora_de_data() -> None:
    assert not list(DATA_DIR.rglob("leaks_2019*"))
