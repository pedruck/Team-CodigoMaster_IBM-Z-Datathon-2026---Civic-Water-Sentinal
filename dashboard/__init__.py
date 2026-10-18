"""Read-only operator dashboard (P6).

It reads alerts and status only through an ``AlertSource`` and imports nothing from the
backend except the contract models in ``sentinel.schemas`` (spec: ``.scratch/
p6-dashboard-evaluation``). Leak labels from the evaluation area never enter this package.
"""
