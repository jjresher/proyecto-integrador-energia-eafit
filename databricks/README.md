# Databricks: pipeline Medallion (SI7006)

Catálogo y esquema: `workspace.eafit_energia` (Databricks Free Edition, cómputo *serverless*, tablas Delta en Unity Catalog).
Los archivos originales llegan a Volumes (`landing` → `procesados` o `cuarentena`) y cada capa escribe sus tablas con un
registro de filas y tiempos en `pipeline_metricas`.

> 🌐 En este repositorio público los notebooks tienen los códigos de las subestaciones anonimizados (SUB1 a SUB9) y
> no incluyen la relación subestación ↔ catálogo de transformadores. La versión ejecutable está en el repositorio privado.

## Notebooks (en el orden del Job)

| Orden | Notebook | Lee | Escribe |
|---|---|---|---|
| 1 | [`01_bronze.py`](01_bronze.py) | Excel y CSV del Volume `landing`; clima de la API de NASA POWER | `bronze_catalogo_fuentes`, `bronze_*_raw` (consumo, resumen, catálogo, demanda, clima, calendario) y `bronze_ingesta_log`. Todo como texto, tal como llega; mueve cada archivo a `procesados` o `cuarentena` |
| 2 | [`02_silver.py`](02_silver.py) | Bronze | `silver_mapeo_subestaciones`, `silver_consumo` (malla horaria completa, valor original `kwh`, `kwh_limpio` y causa de invalidez), `silver_demanda_total`, `silver_demanda_conciliacion`, `silver_clima` |
| 3 | [`03_gold.py`](03_gold.py) | Silver | `dim_calendario`, `gold_modelo_horario`, `gold_consumo_diario`, `gold_perfil_hora_dia`, `gold_conciliacion_total` |
| 4 | [`04_resultados.py`](04_resultados.py) | Resultados del modelo en el Volume (CSV y `.joblib`) | `gold_pronostico_emitido`, `gold_real_vs_pronostico`, `gold_metricas_test`, `gold_ranking_validacion`, `gold_alertas_*`, `gold_umbrales_alerta`, `gold_registro_limpieza`, `gold_modelos`, más el registro en MLflow |

El modelo se entrena en el notebook de [`modelo/`](../modelo/); `04_resultados` publica sus resultados en Gold y comprueba en
Spark que el error coincide con el del notebook (WAPE 9,315 %). La [API](../api/) y el tablero consumen esas tablas.

Formato: archivo fuente de Databricks (`.py`, *File → Export → Source file*). Se importan con *Workspace → Import*.

## Job

`pipeline_energia_eafit`: cuatro tareas encadenadas (01 → 02 → 03 → 04), programado a las 00:00 (America/Bogota) y en pausa
para cuidar la cuota de la Free Edition. Definición en [`jobs/pipeline_energia_eafit.json`](jobs/pipeline_energia_eafit.json). Corrida de referencia: 4 min 16 s, 1.300.169 filas leídas y 334.144 escritas. Ver
[`jobs/`](jobs/).
