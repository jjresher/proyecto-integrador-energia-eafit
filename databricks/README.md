# Databricks: pipeline Medallion (SI7006)

Catálogo y esquema: `workspace.eafit_energia` (Databricks Free Edition, cómputo *serverless*).

## Notebooks

| Orden | Notebook | Qué hace | Estado |
|---|---|---|---|
| 1 | `01_bronze` | Ingesta de los archivos del Volume a tablas Bronze, sin transformar | ⏳ Lo sube el equipo |
| 2 | `02_silver` | Tipos, una lectura por hora, marcas de calidad, conciliación | ⏳ Lo sube el equipo |
| 3 | `03_gold` | Tablas de análisis para el tablero | ⏳ Lo sube el equipo |
| 4 | [`04_resultados.py`](04_resultados.py) | Carga los resultados del modelo (CSV y `.joblib` del Volume) a tablas Gold y registra las versiones del modelo | ✅ Listo |

Formato: los notebooks se guardan como **archivo fuente de Databricks** (`.py`, se exportan con *File → Export → Source file*)
o como `.ipynb`. Los dos se importan de nuevo con *Workspace → Import*.

## `04_resultados`

- **Entrada:** la carpeta del modelo en el Volume (widget `ruta_modelo`, por defecto
  `/Volumes/workspace/eafit_energia/bronze/modelo`), con `resultados/*.csv` y `modelos/*.joblib`. Si el notebook del modelo
  corrió dentro de Databricks, ya los dejó ahí.
- **Salida:** las tablas `gold_*` descritas en [`docs/arquitectura.md`](../docs/arquitectura.md#tablas-gold-que-produce-04_resultados).
- **Verificación:** recalcula en Spark el error del modelo y comprueba que coincide con el reportado por el notebook (WAPE 9,315 %).

## Job

La definición del Job que encadena 01 → 02 → 03 → 04 va en [`jobs/`](jobs/).

## Tablas del pipeline

*(Se completa cuando el equipo suba sus notebooks: nombre, capa, contenido y número de filas de cada tabla.)*

El notebook del modelo espera estas tablas de Silver (los nombres se ajustan en su celda 2.1 si son otros):

| Tabla | Columnas que usa el modelo |
|---|---|
| `silver_consumo` | marca de tiempo, `subestacion`, `kwh_original`, marca de calidad |
| `silver_clima` | marca de tiempo, `T2M`, `PRECTOTCORR`, `ALLSKY_SFC_SW_DWN` |
| `silver_demanda_total` | marca de tiempo, `kwh_original` |
| `silver_dim_subestacion` | `subestacion`, `capacidad_kva` |
