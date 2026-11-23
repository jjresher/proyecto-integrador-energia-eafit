# Job de Databricks: `pipeline_energia_eafit`

| Tarea | Notebook | Depende de |
|---|---|---|
| 1 | `01 bronze` | — |
| 2 | `02 silver` | 1 |
| 3 | `03 gold` | 2 |
| 4 | `04_resultados` | 3 |

- **Programación:** todos los días a las 00:00, zona America/Bogota. En pausa para no gastar la cuota de la Free Edition; se
  corre a mano con *Run now*.
- **Cómputo:** *serverless*.
- **Corrida de referencia:** las cuatro tareas en verde, 4 min 16 s, 83 consultas, 1.300.169 filas leídas y 334.144 escritas
  (capturas en el documento de evidencias del equipo).

*Pendiente:* la definición completa en JSON. Se copia en Databricks desde *Jobs & Pipelines → pipeline_energia_eafit → ⋮ →
View as code → JSON → Copy* y se guarda aquí como `job_pipeline_eafit.json`.
