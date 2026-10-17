# Notebooks

Notebooks são para experimentos e demonstrações. O código que o sistema executa
fica em `src/sentinel/` e é importado pelos notebooks.

- Cada área edita só a própria subpasta (`p1_data/`, `p2_forecast/`, ...).
- Funcionou ou vai ser usado em dois lugares? Mova para `src/sentinel/<área>/`.
- Rode `nbstripout --install` uma vez no seu clone para não commitar saídas.

Primeira célula de todo notebook (na VM):

```python
import sys, os
from pathlib import Path

EU = "seu-nome"
REPO = Path.home() / "shared" / "civicwater" / "users" / EU / "repo"
sys.path.insert(0, str(REPO / "src"))          # importa o SEU clone

for v in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"]:
    os.environ[v] = "1"                        # 3 CPUs para 6 pessoas

%load_ext autoreload
%autoreload 2

import sentinel
print("sentinel de:", Path(sentinel.__file__).parent)
```

No Colab: `!git clone <repo> repo` e `sys.path.insert(0, "/content/repo/src")`.
