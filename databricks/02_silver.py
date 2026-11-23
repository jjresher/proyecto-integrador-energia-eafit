# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC > 🌐 **Versión pública.** Los códigos de las subestaciones están anonimizados (SUB1 a SUB9) y se omitió la relación
# MAGIC > entre cada subestación y su ítem del catálogo de transformadores (y por tanto su capacidad). La lógica es la misma
# MAGIC > que corre en Databricks; la versión ejecutable está en el repositorio privado del equipo.

# COMMAND ----------

# MAGIC %md
# MAGIC # 02 · Silver — datos limpios, tipados y con calidad documentada
# MAGIC Lee Bronze y produce:
# MAGIC
# MAGIC | Tabla | Contenido |
# MAGIC |---|---|
# MAGIC | `silver_consumo` | una fila por subestación y hora (malla completa), con `kwh` original, `kwh_limpio` y flags |
# MAGIC | `silver_calidad` | resumen de calidad por subestación |
# MAGIC | `silver_anomalias` | cada negativo y cada pico detectado (para documentar decisiones) |
# MAGIC | `silver_conciliacion` | suma mensual de las hojas vs. hoja `Resumen` |
# MAGIC | `silver_mapeo_subestaciones` | hoja de datos ↔ ítem del catálogo (con nivel de confianza) |
# MAGIC
# MAGIC **Reglas de limpieza (no se borra nada: se marca)**
# MAGIC * `kwh` conserva el valor original. `kwh_limpio` es nulo cuando el dato es nulo, negativo o un pico.
# MAGIC * **Pico** = `kwh` > `UMBRAL_PICO_X` × percentil 99 de esa subestación (los errores observados son de miles de kWh; lo normal llega a ~270).
# MAGIC * **Huecos** (horas con `kwh_limpio` nulo seguidas): corto ≤ 3 h (interpolable en Gold), largo ≥ 24 h (se excluye del entrenamiento), medio = el resto.
# MAGIC * **Ceros**: se marcan (`flag_cero`) pero se conservan; hay que revisar si son apagones reales o fallas.
# MAGIC * No se suman subestaciones aquí: antes hay que resolver con EAFIT qué mide cada medidor (posible doble conteo).
# MAGIC * **Alcance:** solo campus Medellín (Llanogrande —ítem 12— queda excluido).

# COMMAND ----------

# Celda 1: Configuración
import re
import pandas as pd
from pyspark.sql import functions as F, Window

CATALOG = "workspace"
SCHEMA = "eafit_energia"

UMBRAL_PICO_X = 3
HUECO_CORTO_H = 3
HUECO_LARGO_H = 24
TOLERANCIA_CONCILIACION = 0.005

def T(nombre):
    return f"{CATALOG}.{SCHEMA}.{nombre}"

spark.conf.set("spark.sql.session.timeZone", "America/Bogota")

def guardar(df, nombre):
    (df.write.format("delta").mode("overwrite")
       .option("overwriteSchema", "true")
       .saveAsTable(T(nombre)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Mapeo hoja de datos ↔ catálogo (Campus Medellín)
# MAGIC Cada hoja de datos se relaciona con su ítem del catálogo de transformadores (confirmado con el área técnica de EAFIT).
# MAGIC En la versión pública se omite esa relación, porque junto con el catálogo revela la capacidad de cada subestación.

# COMMAND ----------

# Celda 2: Mapeo hoja de datos <-> ítem del catálogo + capacidades físicas (T2)
# Versión pública: las 9 filas reales (código, ítem del catálogo, bloque, nota) están en el repositorio privado.
mapeo = spark.createDataFrame([
    # ("SUBn", "<ítem del catálogo>", "<bloque>", "confirmado"),
], "subestacion string, item_catalogo string, bloque string, nota string")

cat_med = (spark.table(T("bronze_catalogo_raw"))
           .filter(F.col("item") != "12")
           .select(
               F.col("item").alias("item_catalogo"),
               F.expr("try_cast(capacidad_del_transformador_kva as double)").alias("capacidad_kva"),
               F.col("bloques_atendidos_por_la_subestacion").alias("bloques_atendidos")
           ))

mapeo_cap = mapeo.join(cat_med, "item_catalogo", "left")
guardar(mapeo_cap, "silver_mapeo_subestaciones")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Tipado de Bronze

# COMMAND ----------

# Celda 3: Tipado, malla completa y Flags de calidad confirmados (T2)
bronze = spark.table(T("bronze_consumo_raw"))

p = (bronze
     .withColumn("fecha", F.expr("try_cast(substring(fecha_raw, 1, 10) as date)"))
     .withColumn("hora", F.expr("try_cast(substring(hora_raw, 1, 2) as int)"))
     .withColumn("kwh", F.expr("try_cast(regexp_replace(kwh_raw, ',', '.') as double)"))
     .filter(F.col("fecha").isNotNull() & F.col("hora").isNotNull())
     .withColumn("ts", F.timestamp_seconds(F.unix_timestamp(F.col("fecha").cast("timestamp")) + F.col("hora") * 3600))
     .dropDuplicates(["subestacion", "ts"]))

t0, t1 = p.agg(F.min("ts"), F.max("ts")).first()
malla = (spark.range(1)
         .select(F.explode(F.sequence(F.lit(t0), F.lit(t1), F.expr("interval 1 hour"))).alias("ts"))
         .crossJoin(p.select("subestacion").distinct()))

base = (malla.join(p.select("subestacion", "ts", "kwh", "archivo_origen", "fila_origen"), ["subestacion", "ts"], "left")
        .withColumn("fila_existe", F.col("fila_origen").isNotNull())
        .withColumn("fecha", F.to_date("ts"))
        .withColumn("hora", F.hour("ts"))
        .join(mapeo_cap.select("subestacion", "capacidad_kva"), "subestacion", "left"))

umbrales = (base.groupBy("subestacion")
            .agg(F.expr("percentile_approx(kwh, 0.99)").alias("p99"))
            .withColumn("umbral_pico", F.col("p99") * F.lit(UMBRAL_PICO_X)))

d = (base.join(umbrales, "subestacion")
     .withColumn("causa_invalido",
         F.when(~F.col("fila_existe"), "fila_faltante")
          .when(F.col("kwh").isNull(), "nulo")
          .when(F.col("kwh") < 0, "negativo")
          .when(F.col("kwh") == 0, "cero")
          .when(F.col("capacidad_kva").isNotNull() & (F.col("kwh") > F.col("capacidad_kva")), "sobre_capacidad")
          .when(F.col("kwh") > F.col("umbral_pico"), "pico"))
     .withColumn("es_invalido", F.col("causa_invalido").isNotNull())
     .withColumn("kwh_limpio", F.when(~F.col("es_invalido"), F.col("kwh")))
     .withColumn("regimen_sub3",
         F.when(F.col("subestacion") != "SUB3", "unico")
          .when(F.col("fecha") < F.lit("2026-03-05"), "anterior")
          .otherwise("actual")))

w_orden = Window.partitionBy("subestacion").orderBy("ts")
w_tipo = Window.partitionBy("subestacion", "es_invalido").orderBy("ts")
d = (d.withColumn("rn", F.row_number().over(w_orden))
      .withColumn("rn_tipo", F.row_number().over(w_tipo))
      .withColumn("grupo", F.col("rn") - F.col("rn_tipo"))
      .withColumn("racha_h", F.count(F.lit(1)).over(Window.partitionBy("subestacion", "es_invalido", "grupo")))
      .withColumn("tipo_hueco",
          F.when(F.col("es_invalido") & (F.col("racha_h") >= HUECO_LARGO_H), "largo")
           .when(F.col("es_invalido") & (F.col("racha_h") <= HUECO_CORTO_H), "corto")
           .when(F.col("es_invalido"), "medio"))
      .withColumn("usable_entrenamiento", ~F.col("es_invalido")))

silver = d.select(
    "subestacion", "ts", "fecha", "hora", "kwh", "kwh_limpio",
    "causa_invalido", "tipo_hueco", "racha_h", "regimen_sub3",
    "capacidad_kva", "usable_entrenamiento", "archivo_origen", "fila_origen"
)
guardar(silver, "silver_consumo")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Malla completa subestación × hora
# MAGIC Así una hora que falte en el archivo aparece explícitamente (como `fila_faltante`) en vez de pasar desapercibida.

# COMMAND ----------

# Celda 4: Procesamiento Silver de Demanda Total (medidor general) y Clima (si existen en Bronze)
tablas_existentes = [r.tableName for r in spark.sql(f"SHOW TABLES IN {CATALOG}.{SCHEMA}").collect()]

if "bronze_demanda_raw" in tablas_existentes:
    dem_raw = spark.table(T("bronze_demanda_raw"))
    dem_tip = (dem_raw
               .withColumn("fecha", F.expr("try_to_date(trim(fecha_raw), 'd/M/yyyy')"))
               .withColumn("es_total_dia", F.lower(F.col("hora_raw")).contains("total"))
               .withColumn("hora", F.when(~F.col("es_total_dia"), F.expr("try_cast(regexp_extract(hora_raw, '(\\\\d+)', 1) as int)")))
               .withColumn("kwh", F.expr("try_cast(regexp_replace(kwh_raw, ',', '.') as double)"))
               .filter(F.col("fecha").isNotNull()))

    dem_horaria = (dem_tip.filter(~F.col("es_total_dia") & F.col("hora").isNotNull())
                   .withColumn("ts", F.timestamp_seconds(F.unix_timestamp(F.col("fecha").cast("timestamp")) + F.col("hora") * 3600))
                   .withColumn("flag_nulo", F.col("kwh").isNull())
                   .withColumn("flag_cero", F.col("kwh") == 0)
                   .withColumn("kwh_limpio", F.when((F.col("kwh").isNotNull()) & (F.col("kwh") > 0), F.col("kwh")))
                   .select("ts", "fecha", "hora", "kwh", "kwh_limpio", "flag_nulo", "flag_cero", "archivo_origen"))
    guardar(dem_horaria, "silver_demanda_total")

    # Conciliación diaria de demanda total (debe dar 0 días con diferencia > 1 kWh)
    tot_rep = dem_tip.filter(F.col("es_total_dia")).groupBy("fecha").agg(F.first("kwh").alias("kwh_reportado"))
    tot_sum = dem_horaria.groupBy("fecha").agg(F.sum("kwh").alias("kwh_suma_24h"))
    conc_dem = (tot_rep.join(tot_sum, "fecha", "outer")
                .withColumn("dif_kwh", F.abs(F.col("kwh_reportado") - F.col("kwh_suma_24h"))))
    guardar(conc_dem, "silver_demanda_conciliacion")
    print("Días demanda total con dif > 1 kWh:", conc_dem.filter(F.col("dif_kwh") > 1.0).count())

if "bronze_clima_raw" in tablas_existentes:
    cli_raw = spark.table(T("bronze_clima_raw"))
    cols_cli = cli_raw.columns
    # Soporta tanto formato de API (fecha_hora_nasa = YYYYMMDDHH) como CSV con columnas estándar
    if "fecha_hora_nasa" in cols_cli:
        cli_clean = (cli_raw
                     .withColumn("ts", F.to_timestamp("fecha_hora_nasa", "yyyyMMddHH"))
                     .withColumn("fecha", F.to_date("ts"))
                     .withColumn("hora", F.hour("ts")))
    else:
        col_ts = "timestamp" if "timestamp" in cols_cli else ("fecha_hora" if "fecha_hora" in cols_cli else cols_cli[0])
        cli_clean = (cli_raw
                     .withColumn("ts", F.to_timestamp(F.col(col_ts)))
                     .withColumn("fecha", F.to_date("ts"))
                     .withColumn("hora", F.hour("ts")))

    vars_meteo = [c for c in ["t2m", "rh2m", "prectotcorr", "allsky_sfc_sw_dwn", "clrsky_sfc_sw_dwn"] if c in cols_cli]
    for v in vars_meteo:
        cli_clean = cli_clean.withColumn(v, F.expr(f"try_cast({v} as double)")).withColumn(v, F.when(F.col(v) <= -900, None).otherwise(F.col(v)))

    cli_clean = cli_clean.filter(F.col("ts").isNotNull()).select(["ts", "fecha", "hora"] + vars_meteo)
    guardar(cli_clean, "silver_clima")
    print("Filas en silver_clima:", cli_clean.count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Flags de calidad

# COMMAND ----------

# Celda 5: Resumen de calidad Silver
s = spark.table(T("silver_consumo"))
display(s.groupBy("causa_invalido").count().orderBy(F.desc("count")))
