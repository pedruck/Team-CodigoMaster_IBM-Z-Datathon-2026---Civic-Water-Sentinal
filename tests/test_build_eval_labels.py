"""Testes do gabarito de 2019 (P1, Q-DATA-07).

O YAML abaixo é inventado: p1 foi reparado em 2018, p2 é herdado de 2018, p3 é de 2019.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from sentinel.eval.build_labels import ativos_em_2019, ler_vazamentos_yaml
from sentinel.eval.labels import check_official_labels, read_labels

YAML = """leakages:
- p1, 2018-03-05 15:45, 2018-03-23 10:25, 0.022916, abrupt, 2018-03-05 15:45
- p2, 2018-01-08 13:30, 2019-12-31 23:55, 0.011843, incipient, 2018-01-25 08:30
- p3, 2019-03-01 10:00, 2019-03-10 12:00, 0.02, abrupt, 2019-03-01 10:00
pressure_sensors:
- n1
- p227
"""

DATA_DIR = Path(os.environ.get("SENTINEL_DATA_DIR", "/nao-existe")).expanduser()
EVAL_V2 = DATA_DIR.parent / "eval" / "v2" / "leaks_2019.csv"


def test_le_so_as_linhas_de_vazamento() -> None:
    df = ler_vazamentos_yaml(YAML)
    assert list(df["pipe"]) == ["p2", "p1", "p3"]
    assert list(df.columns) == ["pipe", "start", "end", "leak_size", "type", "peak"]


def test_ativos_em_2019_tira_os_reparados_em_2018() -> None:
    assert list(ativos_em_2019(ler_vazamentos_yaml(YAML))["pipe"]) == ["p2", "p3"]


def test_herdado_mantem_inicio_e_pico_de_2018() -> None:
    df = ler_vazamentos_yaml(YAML)
    p2 = df[df["pipe"] == "p2"].iloc[0]
    assert p2["start"] == pd.Timestamp("2018-01-08 13:30")
    assert p2["peak"] == pd.Timestamp("2018-01-25 08:30")
    assert p2["leak_size"] == 0.011843


def test_yaml_sem_vazamentos_falha() -> None:
    with pytest.raises(ValueError):
        ler_vazamentos_yaml("pressure_sensors:\n- n1\n")


@pytest.mark.skipif(not EVAL_V2.exists(), reason="eval/v2 indisponível")
def test_eval_v2_bate_com_a_lista_oficial() -> None:
    labels = read_labels(EVAL_V2)
    check_official_labels(labels)
    assert len(labels) == 23
