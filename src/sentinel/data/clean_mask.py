"""Máscara das janelas "limpas" de 2018 para treino (P1, item 4).

Uso como script (mostra quanto de 2018 sobra em cada política):
    python -m sentinel.data.clean_mask

Uso no código (P2):
    from sentinel.data.clean_mask import carregar_vazamentos, mascara_limpa
    df = pd.read_csv(".../v1/scada_2018.csv.gz", index_col="timestamp", parse_dates=True)
    treino = df[mascara_limpa(df.index, carregar_vazamentos())]

Políticas:
  reparados (padrão): exclui [início, reparo] de cada vazamento reparado em 2018, ou seja,
      os do relatório de reparos. Os 4 nunca reparados (p257, p427, p654, p810) são pequenos,
      incipientes e continuam ativos em 2019: fazem parte do "normal" que o detector encontra.
  reparados+crescimento: também exclui [início, pico] dos não reparados (fase em que crescem).
  todos: exclui qualquer vazamento ativo (sobra cerca de 2% do ano).

margem_janelas: janelas extras excluídas depois de cada reparo (padrão 0).
Só usa leaks_2018.csv (ano de treino). Nunca lê o gabarito de 2019.
"""
import argparse
import os
from pathlib import Path

import pandas as pd

POLITICAS = ("reparados", "reparados+crescimento", "todos")


def carregar_vazamentos(data_dir=None, version="v1") -> pd.DataFrame:
    base = Path(data_dir or os.environ["SENTINEL_DATA_DIR"]).expanduser()
    return pd.read_csv(base / version / "leaks_2018.csv", parse_dates=["start", "end", "peak"])


def _alinhar(t: pd.Timestamp, index: pd.DatetimeIndex) -> pd.Timestamp:
    return t.tz_localize("UTC") if index.tz is not None and t.tzinfo is None else t


def mascara_limpa(index: pd.DatetimeIndex, vazamentos: pd.DataFrame,
                  politica: str = "reparados", margem_janelas: int = 0) -> pd.Series:
    """True = janela limpa (pode treinar); False = afetada por vazamento."""
    if politica not in POLITICAS:
        raise ValueError(f"política desconhecida: {politica}; use uma de {POLITICAS}")
    limpo = pd.Series(True, index=index, name="limpo")
    margem = pd.Timedelta(minutes=5 * margem_janelas)
    for v in vazamentos.itertuples():
        inicio = _alinhar(v.start, index)
        if v.repaired_in_2018:
            limpo[inicio:_alinhar(v.end, index) + margem] = False
        elif politica == "todos":
            limpo[inicio:_alinhar(v.end, index)] = False
        elif politica == "reparados+crescimento":
            limpo[inicio:_alinhar(v.peak, index)] = False
    return limpo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--data-dir", default=os.environ.get("SENTINEL_DATA_DIR"))
    ap.add_argument("--margem", type=int, default=0)
    args = ap.parse_args(argv)

    vaz = carregar_vazamentos(args.data_dir, args.version)
    index = pd.date_range("2018-01-01", "2018-12-31 23:55", freq="5min")
    for p in POLITICAS:
        m = mascara_limpa(index, vaz, p, args.margem)
        print(f"{p:<24} {m.mean():6.1%} limpo ({m.sum()} de {len(m)} janelas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
