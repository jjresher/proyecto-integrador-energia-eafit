# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Resultados del modelo → Delta (capa Gold)
# MAGIC
# MAGIC **Proyecto integrador 1 · Pronóstico de la energía comprada a la red, campus EAFIT (Medellín)**
# MAGIC
# MAGIC El modelo (LightGBM ajustado, elegido entre varias familias y un ensamble) se entrena en el notebook `Pronostico_energia_EAFIT`
# MAGIC (versión 4), que lee sus datos de las tablas Silver de este pipeline. Ese notebook puede correr dentro de Databricks (tarda
# MAGIC 25-30 minutos o más en el cómputo gratuito) o en un computador con el conector SQL. Lo que llega aquí son **sus salidas**: el
# MAGIC modelo guardado (`.joblib`), su ficha y los CSV de resultados.
# MAGIC Este notebook las carga a tablas Delta para que el tablero, la API y las consultas SQL lean de tablas y no de archivos sueltos
# MAGIC (criterio 6 de SI7006: *resultados almacenados en el motor SQL y consumidos desde un dashboard o consultas*).
# MAGIC
# MAGIC **Antes de correr:** la carpeta del modelo debe estar en el Volume con esta estructura (`ruta_modelo` en los widgets). Si el
# MAGIC notebook del modelo corrió dentro de Databricks, ya la dejó ahí; si corrió en un computador, la sube su celda 14.4b o se sube a mano.
# MAGIC
# MAGIC ```
# MAGIC <ruta_modelo>/resultados/*.csv          (los CSV que genera el notebook del modelo)
# MAGIC <ruta_modelo>/modelos/modelo_energia_v4.joblib   (y las versiones anteriores, si se quieren registrar)
# MAGIC <ruta_modelo>/modelos/ficha_modelo_v4.json
# MAGIC ```
# MAGIC
# MAGIC | Tabla | Contenido |
# MAGIC |---|---|
# MAGIC | `gold_real_vs_pronostico` | **Para el tablero:** cada hora del test con kWh real, pronóstico del modelo elegido, regla "semana pasada", errores y COP |
# MAGIC | `gold_pronosticos_test` | Pronósticos del test de todos los modelos (83 emisiones × 216 filas), con banderas de cobertura |
# MAGIC | `gold_pronostico_emitido` | Último pronóstico emitido (24 h × 9 subestaciones) con banda de alerta y costo |
# MAGIC | `gold_metricas_test`, `gold_ranking_validacion` | Métricas del test y ranking de validación |
# MAGIC | `gold_alertas_validacion`, `gold_alertas_test`, `gold_umbrales_alerta` | Selección del umbral de alertas y su reevaluación |
# MAGIC | `gold_dias_atipicos` | Diferencia real − pronóstico en días atípicos, en kWh y COP |
# MAGIC | `gold_registro_limpieza` | Cada hora descartada por la limpieza del modelo, con su valor original y el motivo (R1 a R4) |
# MAGIC | `gold_resumen_correcciones` | Tabla "anterior / corregido / causa" del notebook del modelo |
# MAGIC | `gold_modelos` | Registro de versiones del modelo: métricas, fecha, archivo y hash |
# MAGIC | `pipeline_metricas` | Filas y segundos de cada carga (evidencia de procesamiento para el criterio 5) |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Configuración

# COMMAND ----------

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone

from pyspark.sql import functions as F

dbutils.widgets.text('catalogo', 'workspace', 'Catálogo')
dbutils.widgets.text('esquema', 'eafit_energia', 'Esquema')
dbutils.widgets.text('ruta_modelo', '/Volumes/workspace/eafit_energia/bronze/modelo', 'Carpeta del modelo en el Volume')

CATALOGO = dbutils.widgets.get('catalogo')
ESQUEMA = dbutils.widgets.get('esquema')
RUTA_MODELO = dbutils.widgets.get('ruta_modelo').rstrip('/')
RUTA_RESULTADOS = f'{RUTA_MODELO}/resultados'
RUTA_MODELOS = f'{RUTA_MODELO}/modelos'


def tabla(nombre):
    return f'{CATALOGO}.{ESQUEMA}.{nombre}'


spark.conf.set('spark.sql.session.timeZone', 'America/Bogota')
spark.sql(f'CREATE SCHEMA IF NOT EXISTS {CATALOGO}.{ESQUEMA}')
FECHA_CARGA = datetime.now(timezone.utc).replace(microsecond=0)

assert os.path.isdir(RUTA_RESULTADOS), f'No existe {RUTA_RESULTADOS}: suba la carpeta resultados/ del modelo al Volume'
print('Resultados:', sorted(os.listdir(RUTA_RESULTADOS)))
print('Modelos:', sorted(os.listdir(RUTA_MODELOS)) if os.path.isdir(RUTA_MODELOS) else '(no hay carpeta modelos/)')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Utilidades: nombres válidos para Delta, comentarios y métricas del pipeline
# MAGIC
# MAGIC Delta no admite espacios, paréntesis ni `%` en los nombres de columna, y los CSV del modelo los tienen
# MAGIC (`LightGBM ajustado`, `Seasonal naive (semana pasada)`, `WAPE_%`). Se normalizan: minúsculas, sin tildes, `%` → `pct`.

# COMMAND ----------

def nombre_columna(texto):
    t = str(texto).replace('%', 'pct')
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'[^a-z0-9]+', '_', t).strip('_')
    return f'q_{t}' if t[:1].isdigit() else (t or 'columna')


def comentar(nombre_tabla, texto):
    """Diccionario de datos: comentario de la tabla (no detiene el notebook si el motor no lo admite)."""
    try:
        spark.sql(f"COMMENT ON TABLE {tabla(nombre_tabla)} IS '{texto.replace(chr(39), chr(39) * 2)}'")
    except Exception as e:                                            # noqa: BLE001
        print(f'  (sin comentario en {nombre_tabla}: {type(e).__name__})')


def registrar_metrica(tabla_destino, filas, segundos, detalle=''):
    (spark.createDataFrame([('04_resultados', tabla_destino, int(filas), float(segundos), detalle)],
                           'notebook string, tabla string, filas long, segundos double, detalle string')
     .withColumn('fecha', F.lit(FECHA_CARGA).cast('timestamp'))
     .write.mode('append').saveAsTable(tabla('pipeline_metricas')))


def leer_csv(archivo, indice=None):
    """CSV del modelo -> DataFrame de Spark con nombres válidos. 'indice' nombra la primera columna sin encabezado."""
    df = (spark.read.option('header', True).option('inferSchema', True)
          .option('multiLine', True).option('escape', '"')
          .csv(f'{RUTA_RESULTADOS}/{archivo}'))
    nuevos = []
    for c in df.columns:
        if indice and (c.startswith('_c0') or c == ''):
            nuevos.append(indice)
        else:
            nuevos.append(nombre_columna(c))
    return df.toDF(*nuevos)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Carga de los CSV a tablas Delta

# COMMAND ----------

ARCHIVOS = [
    # archivo,                          tabla,                       columna índice, comentario
    ('pronosticos_test.csv',            'gold_pronosticos_test',     None,
     'Pronósticos del periodo de test (1-ago a 22-oct-2026): 83 emisiones a las 00:00 x 216 filas, todos los modelos, kWh real y banderas de cobertura'),
    ('metricas_test.csv',               'gold_metricas_test',        'modelo',
     'Métricas del test por modelo sobre las mismas filas comparables (WAPE, MAE, RMSE, sesgo, MASE)'),
    ('ranking_validacion.csv',          'gold_ranking_validacion',   None,
     'Ranking de modelos en la validación walk-forward (abr-jul 2026)'),
    ('registro_limpieza.csv',           'gold_registro_limpieza',    None,
     'Horas descartadas por la limpieza causal del modelo, con su valor original y el motivo (R1 a R4)'),
    ('alertas_validacion.csv',          'gold_alertas_validacion',   None,
     'Selección del umbral de alertas, solo con datos de validación'),
    ('alertas_test_retrospectivo.csv',  'gold_alertas_test',         'indicador',
     'Reevaluación retrospectiva de las alertas en el test con el umbral congelado en validación'),
    ('umbrales_alerta_relativos.csv',   'gold_umbrales_alerta',      None,
     'Umbrales de alerta relativos al nivel de 7 días, por subestación y cuantil'),
    ('diferencia_dias_atipicos.csv',    'gold_dias_atipicos',        None,
     'Diferencia observado - pronosticado por subestación y día atípico, en kWh y COP estimados'),
    ('resumen_correcciones.csv',        'gold_resumen_correcciones', None,
     'Resultado anterior / resultado corregido / causa de la diferencia (notebook del modelo)'),
]

for archivo, destino, indice, comentario in ARCHIVOS:
    if not os.path.exists(f'{RUTA_RESULTADOS}/{archivo}'):
        print(f'⚠ falta {archivo}: se omite {destino}')
        continue
    t0 = time.time()
    df = leer_csv(archivo, indice).withColumn('fecha_carga', F.lit(FECHA_CARGA).cast('timestamp'))
    df.write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(tabla(destino))
    n = spark.table(tabla(destino)).count()
    comentar(destino, comentario)
    registrar_metrica(destino, n, time.time() - t0, archivo)
    print(f'{destino:<28} {n:>7,} filas  ← {archivo}')

# COMMAND ----------

# MAGIC %md
# MAGIC ### Último pronóstico emitido
# MAGIC
# MAGIC Los archivos `pronostico_AAAA-MM-DD.csv` son pronósticos emitidos a las 00:00 de ese día. Se cargan todos en una sola tabla con la
# MAGIC fecha de emisión, para que el tablero y la API muestren siempre el más reciente.

# COMMAND ----------

emitidos = sorted(a for a in os.listdir(RUTA_RESULTADOS) if re.match(r'pronostico_\d{4}-\d{2}-\d{2}\.csv$', a))
assert emitidos, 'No hay archivos pronostico_AAAA-MM-DD.csv en resultados/'
t0 = time.time()
partes = [leer_csv(a).withColumn('emision', F.lit(a[11:21]).cast('date')) for a in emitidos]
pron = partes[0]
for p in partes[1:]:
    pron = pron.unionByName(p)
(pron.withColumn('fecha_carga', F.lit(FECHA_CARGA).cast('timestamp'))
 .write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(tabla('gold_pronostico_emitido')))
comentar('gold_pronostico_emitido', 'Pronósticos emitidos a las 00:00 (24 h x 9 subestaciones) con banda de alerta y costo en COP')
n = spark.table(tabla('gold_pronostico_emitido')).count()
registrar_metrica('gold_pronostico_emitido', n, time.time() - t0, ', '.join(emitidos))
print(f'gold_pronostico_emitido: {n} filas de {len(emitidos)} emisión(es): {emitidos}')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Tabla para el tablero → `gold_real_vs_pronostico`
# MAGIC
# MAGIC Una fila por hora del test con lo que el tablero necesita: kWh real, pronóstico del **modelo elegido** (columna `pred`), la
# MAGIC regla "semana pasada", el error de cada uno y su valor en COP con la tarifa estimada. Se calcula con Spark sobre la tabla Delta.

# COMMAND ----------

t0 = time.time()
pt = spark.table(tabla('gold_pronosticos_test'))
rvp = (pt.select(F.col('emision').cast('date').alias('emision'), 'horizonte_h',
                 F.col('timestamp').cast('timestamp').alias('timestamp'), F.col('dia').cast('date').alias('fecha'),
                 'subestacion',
                 F.col('kwh').alias('kwh_real'),
                 F.col('pred').alias('kwh_pronostico'),
                 F.col('seasonal_naive_semana_pasada').alias('kwh_semana_pasada'),
                 'atipico', 'tiene_medicion', 'comparable', 'tarifa')
       .withColumn('error_kwh', F.col('kwh_pronostico') - F.col('kwh_real'))
       .withColumn('error_abs_kwh', F.abs('error_kwh'))
       .withColumn('error_abs_semana_pasada_kwh', F.abs(F.col('kwh_semana_pasada') - F.col('kwh_real')))
       .withColumn('costo_real_cop', F.col('kwh_real') * F.col('tarifa'))
       .withColumn('costo_pronostico_cop', F.col('kwh_pronostico') * F.col('tarifa'))
       .withColumn('mes', F.trunc('fecha', 'month'))
       .withColumn('fecha_carga', F.lit(FECHA_CARGA).cast('timestamp')))

(rvp.write.mode('overwrite').option('overwriteSchema', 'true').partitionBy('mes')
 .saveAsTable(tabla('gold_real_vs_pronostico')))
comentar('gold_real_vs_pronostico',
         'Test hora a hora: kWh real, pronóstico del modelo elegido y regla semana pasada, errores y COP (tarifa estimada). '
         'Usar comparable = true para comparar contra la regla')
n = spark.table(tabla('gold_real_vs_pronostico')).count()
registrar_metrica('gold_real_vs_pronostico', n, time.time() - t0, 'derivada de gold_pronosticos_test')
print(f'gold_real_vs_pronostico: {n:,} filas')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Registro de versiones del modelo → `gold_modelos`
# MAGIC
# MAGIC Cada `.joblib` de la carpeta `modelos/` se registra con su ficha (modelo, fecha, entrenado hasta, métricas del test), su tamaño y
# MAGIC su hash SHA-256: si alguien cambia el archivo, el hash lo delata. Desde la versión 4 también guarda el **linaje**: de qué tabla de
# MAGIC Silver salió cada dato con el que se entrenó (`fuente_datos_json`). Si MLflow está disponible, además se registra el experimento.

# COMMAND ----------

def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for bloque in iter(lambda: f.read(1 << 20), b''):
            h.update(bloque)
    return h.hexdigest()


filas_modelos = []
if os.path.isdir(RUTA_MODELOS):
    for archivo in sorted(a for a in os.listdir(RUTA_MODELOS) if a.endswith('.joblib')):
        version = archivo.rsplit('_', 1)[-1].replace('.joblib', '')          # modelo_energia_v3.joblib -> v3
        ruta_ficha = f'{RUTA_MODELOS}/ficha_modelo_{version}.json'
        ficha = json.load(open(ruta_ficha)) if os.path.exists(ruta_ficha) else {}
        m = ficha.get('metricas_test', {})
        filas_modelos.append((version, ficha.get('modelo'), ficha.get('creado'), ficha.get('entrenado_hasta'),
                              m.get('WAPE_%'), m.get('MAE_kWh'), m.get('RMSE_kWh'), m.get('sesgo_%'),
                              f'{RUTA_MODELOS}/{archivo}', os.path.getsize(f'{RUTA_MODELOS}/{archivo}'),
                              sha256(f'{RUTA_MODELOS}/{archivo}'),
                              json.dumps(ficha.get('params', {}), ensure_ascii=False),
                              json.dumps(ficha.get('fuente_datos', {}), ensure_ascii=False)))   # v4+: de qué tabla salió cada dato

esquema_modelos = ('version string, modelo string, creado string, entrenado_hasta string, wape_test_pct double, '
                   'mae_test_kwh double, rmse_test_kwh double, sesgo_test_pct double, archivo string, tamano_bytes long, '
                   'sha256 string, parametros_json string, fuente_datos_json string')
(spark.createDataFrame(filas_modelos, esquema_modelos)
 .withColumn('fecha_carga', F.lit(FECHA_CARGA).cast('timestamp'))
 .write.mode('overwrite').option('overwriteSchema', 'true').saveAsTable(tabla('gold_modelos')))
comentar('gold_modelos', 'Registro de versiones del modelo: métricas del test, archivo .joblib y hash SHA-256')
display(spark.table(tabla('gold_modelos')).drop('parametros_json'))   # fuente_datos_json: linaje de los datos de entrenamiento

# COMMAND ----------

# MLflow (opcional): registra el modelo más reciente como artefacto de un experimento de Databricks
try:
    import mlflow
    if filas_modelos:
        ultima = filas_modelos[-1]
        usuario = spark.sql('SELECT current_user()').first()[0]
        mlflow.set_experiment(f'/Users/{usuario}/pronostico_energia_eafit')
        with mlflow.start_run(run_name=f'{ultima[1]} {ultima[0]} (carga 04_resultados)'):
            mlflow.log_params({'version': ultima[0], 'modelo': ultima[1], 'entrenado_hasta': ultima[3], 'sha256': ultima[10]})
            mlflow.log_metrics({k: v for k, v in [('test_wape_pct', ultima[4]), ('test_mae_kwh', ultima[5]),
                                                  ('test_rmse_kwh', ultima[6])] if v is not None})
            mlflow.log_artifact(ultima[8])
        print('Experimento registrado en MLflow:', f'/Users/{usuario}/pronostico_energia_eafit')
except Exception as e:                                                # noqa: BLE001
    print(f'MLflow no disponible aquí ({type(e).__name__}); el modelo queda versionado en gold_modelos')

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Verificación
# MAGIC
# MAGIC El WAPE del modelo elegido, **recalculado en Spark** sobre `gold_real_vs_pronostico` (filas comparables), debe coincidir con el que
# MAGIC reporta el notebook del modelo en `gold_metricas_test`. Si coinciden, la carga no perdió ni alteró filas.

# COMMAND ----------

wape_spark = (spark.table(tabla('gold_real_vs_pronostico')).where('comparable')
              .agg((100 * F.sum('error_abs_kwh') / F.sum(F.abs('kwh_real'))).alias('wape'),
                   (100 * F.sum('error_abs_semana_pasada_kwh') / F.sum(F.abs('kwh_real'))).alias('wape_semana'),
                   F.count('*').alias('filas'))
              .first())
met = spark.table(tabla('gold_metricas_test'))
fila_elegido = met.where(F.col('elegido').isNotNull()).first()
fila_semana = met.where(F.col('modelo').startswith('Seasonal')).first()
print(f"Modelo elegido: {fila_elegido['modelo']}")
print(f"  WAPE recalculado en Spark: {wape_spark['wape']:.3f} %  ·  reportado por el modelo: {fila_elegido['wape_pct']:.3f} %")
print(f"  Regla semana pasada:       {wape_spark['wape_semana']:.3f} %  ·  reportado: {fila_semana['wape_pct']:.3f} %")
print(f"  Filas comparables: {wape_spark['filas']:,}  ·  reportadas: {fila_elegido['filas']:,}")
assert abs(wape_spark['wape'] - fila_elegido['wape_pct']) < 0.01, 'El WAPE recalculado no coincide con el del modelo'
assert abs(wape_spark['wape_semana'] - fila_semana['wape_pct']) < 0.01, 'El WAPE de la regla no coincide'
assert wape_spark['filas'] == fila_elegido['filas'], 'El número de filas comparables no coincide'
print('✔ Resultados cargados y consistentes con el notebook del modelo')

# COMMAND ----------

display(spark.sql(f"""
    SELECT subestacion,
           ROUND(100 * SUM(error_abs_kwh) / SUM(kwh_real), 2)               AS wape_modelo_pct,
           ROUND(100 * SUM(error_abs_semana_pasada_kwh) / SUM(kwh_real), 2) AS wape_semana_pasada_pct,
           ROUND(SUM(costo_real_cop) / 1e6, 1)                              AS costo_real_millones_cop
    FROM {tabla('gold_real_vs_pronostico')}
    WHERE comparable
    GROUP BY subestacion
    ORDER BY wape_modelo_pct
"""))
