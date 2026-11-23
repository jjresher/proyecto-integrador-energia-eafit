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
# MAGIC # 03 · Gold — tabla de características (features) lista para Machine Learning y Visualización
# MAGIC Lee `silver_consumo` y `silver_mapeo_subestaciones` y produce:
# MAGIC
# MAGIC | Tabla | Contenido |
# MAGIC |---|---|
# MAGIC | `gold_features` | dataset completo con variables de calendario, festivos CO, periodo académico, rezagos y medias móviles |
# MAGIC | `gold_train_ready` | subconjunto filtrado (`usable_modelo = True`) listo para entrenar modelos sin NaN en rezagos ni huecos largos |
# MAGIC
# MAGIC **Reglas de negocio y prevención de fuga de datos (data leakage)**
# MAGIC * **Alcance:** solo campus Medellín (9 subestaciones; Llanogrande excluido).
# MAGIC * **Huecos cortos (≤ 3 h):** se interpolan linealmente por subestación (`kwh_modelado`).
# MAGIC * **Huecos medios y largos (> 3 h):** se conservan nulos y quedan marcados con `usable_modelo = False`.
# MAGIC * **Rezagos y ventanas móviles:** calculados exclusivamente con observaciones pasadas (`lag 24h`, `lag 168h`, medias móviles cerradas en `t-1`).
# MAGIC * **SUB1:** solo se habilita para entrenamiento desde noviembre de 2025 (antes no tiene registros).

# COMMAND ----------

# Celda 1: Configuración
from pyspark.sql import functions as F

CATALOG = "workspace"
SCHEMA = "eafit_energia"
TARIFA_COP = 524.02  # Tarifa oficial definida por el equipo

def T(nombre):
    return f"{CATALOG}.{SCHEMA}.{nombre}"

spark.conf.set("spark.sql.session.timeZone", "America/Bogota")
tablas_existentes = [r.tableName for r in spark.sql(f"SHOW TABLES IN {CATALOG}.{SCHEMA}").collect()]

# COMMAND ----------

# Celda 2: dim_calendario (Calendario oficial 2026 + estimado 2025)
s_fechas = spark.table(T("silver_consumo")).select("fecha").distinct()

if "bronze_calendario_raw" in tablas_existentes:
    cal_rangos = (spark.table(T("bronze_calendario_raw"))
                  .withColumn("f_ini", F.to_date("fecha_inicio"))
                  .withColumn("f_fin", F.to_date("fecha_fin"))
                  .filter(F.col("f_ini").isNotNull() & F.col("f_fin").isNotNull())
                  .select(
                      F.explode(F.sequence("f_ini", "f_fin", F.expr("interval 1 day"))).alias("fecha"),
                      F.col("periodo").alias("periodo_academico"),
                      F.col("semestre"),
                      F.col("fuente"),
                      F.col("nota")
                  )
                  .dropDuplicates(["fecha"]))
    
    cal = (s_fechas.join(cal_rangos, "fecha", "left")
           .withColumn("dia_semana", F.dayofweek("fecha"))  # 1=Dom .. 7=Sab
           .withColumn("es_fin_semana", F.col("dia_semana").isin([1, 7]).cast("int"))
           .withColumn("periodo_academico", F.coalesce(F.col("periodo_academico"), F.lit("sin_clasificar"))))
else:
    cal = (s_fechas
           .withColumn("dia_semana", F.dayofweek("fecha"))
           .withColumn("es_fin_semana", F.col("dia_semana").isin([1, 7]).cast("int"))
           .withColumn("periodo_academico",
               F.when((F.col("fecha").between("2025-01-20", "2025-05-31")) |
                      (F.col("fecha").between("2025-07-14", "2025-11-22")) |
                      (F.col("fecha").between("2026-01-19", "2026-05-30")) |
                      (F.col("fecha").between("2026-07-13", "2026-11-21")), "clases")
                .when((F.col("fecha").between("2025-12-20", "2026-01-12")) |
                      (F.col("fecha").between("2026-12-14", "2027-01-11")), "cierre")
                .otherwise("intersemestral")))

(cal.write.format("delta").mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(T("dim_calendario")))

spark.sql(f"COMMENT ON TABLE {T('dim_calendario')} IS 'Dimensión calendario académico EAFIT Medellín (2025 estimado, 2026 oficial).'")
display(spark.table(T("dim_calendario")).limit(10))


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Configuración

# COMMAND ----------

# Celda 3: gold_modelo_horario (Subestación x hora + clima + calendario, particionada por mes)
s = spark.table(T("silver_consumo")).withColumn("mes", F.date_format("fecha", "yyyy-MM"))
cal_df = spark.table(T("dim_calendario"))

gold_hor = s.join(cal_df, "fecha", "left")

if "silver_clima" in tablas_existentes:
    cli_df = spark.table(T("silver_clima")).drop("fecha", "hora")
    gold_hor = gold_hor.join(cli_df, "ts", "left")

(gold_hor.write.format("delta").mode("overwrite")
         .partitionBy("mes")
         .option("overwriteSchema", "true")
         .saveAsTable(T("gold_modelo_horario")))

spark.sql(f"COMMENT ON TABLE {T('gold_modelo_horario')} IS 'Consumo horario limpio de energía comprada a la red por subestación, enriquecido con clima y calendario.'")
spark.sql(f"COMMENT ON COLUMN {T('gold_modelo_horario')}.kwh_limpio IS 'Energía comprada a la red EPM en kWh tras reglas de calidad Silver.'")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Carga desde Silver e interpolación de huecos cortos (≤ 3 h)

# COMMAND ----------

# Celda 4: gold_consumo_diario (kWh y COP por subestación y día, particionada por mes)
gold_dia = (gold_hor
            .groupBy("subestacion", "fecha", "mes", "periodo_academico")
            .agg(
                F.sum("kwh_limpio").alias("kwh_diario"),
                F.round(F.sum("kwh_limpio") * F.lit(TARIFA_COP), 2).alias("costo_cop"),
                F.count(F.when(F.col("usable_entrenamiento"), 1)).alias("horas_validas"),
                F.count("*").alias("horas_totales")
            )
            .withColumn("cobertura_pct", F.round(F.col("horas_validas") * 100.0 / F.col("horas_totales"), 2)))

(gold_dia.write.format("delta").mode("overwrite")
         .partitionBy("mes")
         .option("overwriteSchema", "true")
         .saveAsTable(T("gold_consumo_diario")))

spark.sql(f"COMMENT ON TABLE {T('gold_consumo_diario')} IS 'Agregado diario de energía comprada (kWh) y costo estimado (COP 524.02/kWh) por subestación.'")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Calendario, festivos de Colombia y periodo académico EAFIT

# COMMAND ----------

# Celda 5: gold_perfil_hora_dia (Mapa de calor hora x día de la semana)
gold_perfil = (gold_hor
               .filter(F.col("usable_entrenamiento"))
               .withColumn("dia_semana_num", F.dayofweek("fecha"))
               .groupBy("subestacion", "periodo_academico", "dia_semana_num", "hora")
               .agg(
                   F.round(F.avg("kwh_limpio"), 2).alias("kwh_promedio"),
                   F.round(F.stddev("kwh_limpio"), 2).alias("kwh_desv_est"),
                   F.count("*").alias("n_observaciones")
               ))

(gold_perfil.write.format("delta").mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(T("gold_perfil_hora_dia")))

spark.sql(f"COMMENT ON TABLE {T('gold_perfil_hora_dia')} IS 'Perfil horario promedio por día de la semana y periodo académico para mapas de calor en el tablero.'")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Rezagos (lags) y estadísticas móviles sin fuga de datos
# MAGIC Todo rezago usa estrictamente información pasada (`t - 24h`, `t - 168h`) para que sea válido en pronóstico a 24 horas.

# COMMAND ----------

# Celda 6: gold_conciliacion_total (Medidor general vs suma de 9 subestaciones por día)
if "silver_demanda_total" in tablas_existentes:
    dem_dia = (spark.table(T("silver_demanda_total"))
               .groupBy("fecha")
               .agg(
                   F.sum("kwh_limpio").alias("kwh_medidor_general"),
                   F.count("kwh_limpio").alias("horas_validas_general")
               ))
    sub_dia = (gold_hor
               .groupBy("fecha")
               .agg(
                   F.sum("kwh_limpio").alias("kwh_suma_9_subestaciones"),
                   F.count(F.when(F.col("usable_entrenamiento"), 1)).alias("horas_validas_subs")
               ))
    conc_tot = (dem_dia.join(sub_dia, "fecha", "inner")
                .withColumn("mes", F.date_format("fecha", "yyyy-MM"))
                .withColumn("razon_subs_vs_total", F.round(F.col("kwh_suma_9_subestaciones") / F.col("kwh_medidor_general"), 4))
                .withColumn("diferencia_kwh", F.round(F.col("kwh_medidor_general") - F.col("kwh_suma_9_subestaciones"), 2)))

    (conc_tot.write.format("delta").mode("overwrite")
             .partitionBy("mes")
             .option("overwriteSchema", "true")
             .saveAsTable(T("gold_conciliacion_total")))

    spark.sql(f"COMMENT ON TABLE {T('gold_conciliacion_total')} IS 'Comparación diaria entre el medidor general de EAFIT y la suma de las 9 subestaciones (~95% cobertura).'")
    display(conc_tot.orderBy(F.desc("fecha")).limit(10))

# COMMAND ----------



# COMMAND ----------

# Celda 7: Diccionario de datos (comentarios de columnas)
comentarios = {
    "gold_modelo_horario": {
        "subestacion": "Código de la subestación (SUB2, SUB4, etc.)",
        "ts": "Fecha y hora (zona America/Bogota)",
        "kwh": "Energía comprada a la red EPM en kWh, valor original",
        "causa_invalido": "Motivo de invalidez: fila_faltante, nulo, negativo, cero, sobre_capacidad o pico",
        "regimen_sub3": "anterior/actual al cambio de transformador del 5-mar-2026 (solo SUB3)",
        "capacidad_kva": "Capacidad del transformador en kVA según el catálogo",
        "usable_entrenamiento": "True si la hora es válida para entrenar",
        "mes": "Mes (yyyy-MM), columna de partición",
    },
    "gold_consumo_diario": {
        "kwh_diario": "Suma diaria de kWh válidos",
        "costo_cop": "Costo estimado en COP (kWh x 524,02)",
        "horas_validas": "Horas válidas del día",
        "cobertura_pct": "Porcentaje de horas válidas del día",
        "mes": "Mes (yyyy-MM), columna de partición",
    },
    "gold_perfil_hora_dia": {
        "dia_semana_num": "Día de la semana (1=domingo ... 7=sábado)",
        "kwh_promedio": "Consumo promedio en kWh",
        "kwh_desv_est": "Desviación estándar del consumo",
        "n_observaciones": "Número de observaciones usadas",
    },
    "gold_conciliacion_total": {
        "kwh_medidor_general": "kWh diarios del medidor general",
        "kwh_suma_9_subestaciones": "kWh diarios sumando las 9 subestaciones",
        "razon_subs_vs_total": "Suma de subestaciones / medidor general",
        "diferencia_kwh": "Medidor general menos suma de subestaciones",
    },
}
for tabla, cols in comentarios.items():
    for col, txt in cols.items():
        txt = txt.replace("'", "")
        try:
            spark.sql(f"COMMENT ON COLUMN {T(tabla)}.{col} IS '{txt}'")
        except Exception as e:
            print(f"Omitido {tabla}.{col}: {str(e)[:80]}")
