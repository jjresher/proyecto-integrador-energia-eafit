# Job de Databricks: `pipeline_energia_eafit`

Definición completa: [`pipeline_energia_eafit.json`](pipeline_energia_eafit.json), exportada desde *Jobs & Pipelines →
pipeline_energia_eafit → ⋮ → View as code → JSON*. Se recrea con *Jobs & Pipelines → Create → Job → ⋮ → Edit as code*
(ajustando las rutas de los notebooks al usuario del workspace).

| Tarea | Notebook | Depende de | Corre si |
|---|---|---|---|
| `01_bronze` | `01 bronze` | — | — |
| `02_silver` | `02 silver` | `01_bronze` | la anterior terminó bien |
| `03_gold` | `03 gold` | `02_silver` | la anterior terminó bien |
| `04_resultados` | `04_resultados` | `03_gold` | la anterior terminó bien |

- **Programación:** todos los días a las 00:00:17 (`quartz_cron_expression: 17 0 0 * * ?`), zona `America/Bogota`. Está en
  pausa (`PAUSED`) para no gastar la cuota de la Free Edition; se corre a mano con *Run now*.
- **Ejecución:** una corrida a la vez (`max_concurrent_runs: 1`), con cola habilitada; cómputo *serverless* en modo
  *performance optimized*.
- **Corrida de referencia** (documento de evidencias): las cuatro tareas en verde, 4 min 16 s, 83 consultas, 1.300.169 filas
  leídas y 334.144 escritas.

En esta versión pública, las rutas de los notebooks usan `<usuario>` en lugar del usuario real de Databricks.
