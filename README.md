# CivicWater Sentinel — Team CodigoMaster (IBM Z Datathon 2026)

Detecção de vazamentos em tempo real sobre dados SCADA (BattLeDIM), com inferência auditável no IBM LinuxONE.

| Pasta | Dono |
|---|---|
| sentinel/data | P1 — dados e replay |
| sentinel/forecast | P2 — features e modelos |
| sentinel/api | P3 — backend |
| sentinel/detect | P4 — resíduo, CUSUM, regiões |
| sentinel/audit | P5 — log encadeado e narrativa |
| sentinel/dashboard, sentinel/eval | P6 — dashboard e avaliação |

Dados e modelos NÃO ficam no Git: vivem em `shared/civicwater/data` e `shared/civicwater/models` na VM.
