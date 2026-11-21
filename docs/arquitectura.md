# Arquitectura

## Arquitectura de referencia: *lakehouse* por zonas (Medallion)

| Requisito | Situación del proyecto | Decisión |
|---|---|---|
| **Volumen** | ≈ 120 000 lecturas horarias de consumo, ≈ 14 000 horas de clima, algunos miles de celdas del medidor general: decenas de MB | Un *lakehouse* en Databricks con Spark y tablas Delta. Es más de lo que exige el volumen actual, pero escala si se agregan subestaciones, años o medición cada 15 minutos sin cambiar el diseño |
| **Velocidad** | Los datos llegan por lotes: archivos que entrega la universidad y descargas de clima | Ingesta **batch**; no se necesita *streaming* |
| **Latencia** | El pronóstico se emite una vez al día, a las 00:00, para las 24 horas siguientes | Un Job diario es suficiente |

Por eso se descartan Lambda y Kappa (pensadas para flujos continuos) y se usa un *lakehouse* con zonas raw → trusted → refined
(Bronze → Silver → Gold).

## Flujo de extremo a extremo

| Etapa | Qué pasa | Dónde |
|---|---|---|
| **Origen** | Excel de consumo por subestación, medidor general e inventario (EAFIT); clima horario (API de NASA POWER); calendario académico | [`datos/`](../datos/README.md) |
| **Ingesta** | Los archivos se suben a un Volume de Unity Catalog y Bronze los carga sin transformar, con su archivo de origen y fecha de ingesta | `databricks/01_bronze` |
| **Almacenamiento** | Tablas **Delta** (formato columnar Parquet + registro de transacciones) en el catálogo `workspace.eafit_energia` | Unity Catalog |
| **Procesamiento** | Silver: tipos, una lectura por hora, marcas de calidad, conciliación contra los totales. Gold: tablas listas para análisis y tablero | `databricks/02_silver`, `databricks/03_gold` |
| **Modelo** | Lee de Silver el valor original de cada hora, limpia de forma causal, entrena y evalúa; guarda el modelo versionado (`.joblib` + MLflow) y sus resultados | [`modelo/`](../modelo/) |
| **Despliegue** | Los resultados del modelo se cargan a tablas Gold; el modelo se publica mediante una API | `databricks/04_resultados.py`, [`api/`](../api/) |
| **Consumo** | Tablero y consultas SQL sobre las tablas Gold | [`tablero/`](../tablero/) |
| **Orquestación** | Un Job de Databricks encadena 01 → 02 → 03 → 04 | [`databricks/jobs/`](../databricks/jobs/) |

## Por qué el modelo lee de Silver y no de Gold

La limpieza de Silver describe la calidad de **toda** la historia, para el tablero y la conciliación; sus reglas pueden usar la
serie completa (por ejemplo, un percentil calculado con todos los meses). A las 00:00 el modelo no conoce el futuro, así que
entrenar con esos valores sería una **fuga de información**. El modelo toma el valor original (`kwh_original`) y aplica su propia
limpieza **causal**: cada hora se juzga solo con horas anteriores. Su prueba de no-fuga reconstruye el pronóstico desde el dato
original recortado a las 23:00. La sección 4.1c del notebook compara las dos limpiezas.

## Tablas Gold que produce `04_resultados`

| Tabla | Contenido |
|---|---|
| `gold_real_vs_pronostico` | Cada hora del periodo de prueba: kWh real, pronóstico del modelo, regla "semana pasada", errores y COP (**la principal para el tablero**) |
| `gold_pronosticos_test` | Pronósticos de todos los modelos (83 emisiones × 216 filas) con banderas de cobertura |
| `gold_pronostico_emitido` | Pronóstico emitido para el día siguiente (24 h × 9 subestaciones) con banda de alerta y costo |
| `gold_metricas_test`, `gold_ranking_validacion` | Métricas del test y ranking de la validación |
| `gold_alertas_validacion`, `gold_alertas_test`, `gold_umbrales_alerta` | Umbral de alertas elegido en validación y su reevaluación |
| `gold_dias_atipicos` | Diferencia real − pronóstico en días atípicos |
| `gold_registro_limpieza` | Cada hora que descartó la limpieza del modelo, con su valor original y el motivo |
| `gold_resumen_correcciones` | Tabla "resultado anterior / corregido / causa" |
| `gold_modelos` | Registro de versiones del modelo: métricas, archivo, hash SHA-256 y origen de los datos |

Las tablas de Bronze, Silver y Gold del pipeline se documentan en [`databricks/README.md`](../databricks/README.md) cuando el
equipo suba sus notebooks.

## Credenciales

- Dentro de Databricks no se usan tokens: los notebooks leen el catálogo con los permisos del usuario.
- Desde un computador, el modelo usa un token guardado en un archivo `.env` (plantilla: `modelo/.env.ejemplo`), excluido del
  repositorio por `.gitignore`. Ningún notebook tiene credenciales escritas.
