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
# MAGIC # 01 · Bronze — ingesta de archivos originales
# MAGIC **Regla de Bronze:** se conserva todo tal como llega (todas las columnas como texto) más su procedencia. **No se corrige nada aquí.**
# MAGIC
# MAGIC **Orden de ejecución**
# MAGIC 1. Corra la celda de *Configuración* (crea el esquema y el volumen).
# MAGIC 2. Suba los dos `.xlsx` al volumen: *Catalog → workspace → eafit_energia → Volumes → bronze → Upload to this volume*.
# MAGIC 3. Corra el resto.
# MAGIC
# MAGIC **Tablas que crea:** `bronze_consumo_raw`, `bronze_resumen_raw`, `bronze_catalogo_raw`, `bronze_inventario`.
# MAGIC
# MAGIC **Alcance:** solo campus Medellín (Llanogrande queda fuera; no hay datos de esa sede en el archivo de consumo).

# COMMAND ----------

# Celda 1: Dependencias
%pip install openpyxl requests --quiet

# COMMAND ----------

# Celda 2: Reiniciar Python
dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuración

# COMMAND ----------

# Celda 3: Configuración y utilidades
import os
import re
import shutil
import unicodedata
from datetime import datetime
import requests
import pandas as pd
from pyspark.sql import functions as F, types as types

CATALOG = "workspace"
SCHEMA = "eafit_energia"
VOLUME = "bronze"

RUTA_BASE = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
DIR_LANDING = f"{RUTA_BASE}/landing"
DIR_PROC = f"{RUTA_BASE}/procesados"
DIR_CUAR = f"{RUTA_BASE}/cuarentena"
DIR_MOD = f"{RUTA_BASE}/modelos"

for d in [DIR_LANDING, DIR_PROC, DIR_CUAR, DIR_MOD]:
    os.makedirs(d, exist_ok=True)

FECHA_INGESTA = datetime.now()

def T(nombre):
    return f"{CATALOG}.{SCHEMA}.{nombre}"

def limpiar_nombre(txt):
    txt = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", txt.lower()).strip("_")

def vacia(df):
    return df.isna().all(axis=1)

def a_spark_texto(df, cols):
    df = df[cols].astype(object).where(df[cols].notna(), None)
    schema = types.StructType([types.StructField(c, types.StringType(), True) for c in cols])
    return spark.createDataFrame(df, schema=schema)

def guardar(df, nombre, modo="overwrite"):
    (df.write.format("delta").mode(modo)
       .option("overwriteSchema", "true")
       .saveAsTable(T(nombre)))

def registrar_log(fuente, archivo, filas, estado, mensaje=""):
    log_df = (spark.createDataFrame(
        [(fuente, archivo, int(filas), estado, str(mensaje)[:500])],
        "fuente string, archivo string, filas int, estado string, mensaje string"
    ).withColumn("ts", F.current_timestamp()))
    log_df.write.format("delta").mode("append").saveAsTable(T("bronze_ingesta_log"))

def mover_archivo(ruta_origen, destino_dir, nombre_archivo):
    ts_tag = FECHA_INGESTA.strftime("%Y%m%d_%H%M%S")
    ruta_dest = f"{destino_dir}/{ts_tag}_{nombre_archivo}"
    shutil.move(ruta_origen, ruta_dest)

# COMMAND ----------

# Celda 4: Catálogo de fuentes (Criterio 3)
catalogo_fuentes = [
    ("Subestaciones (9 hojas)", "Medidores de energía comprada a EPM", "xlsx (largo)", "120.096 filas (15-abr-2025 a 22-oct-2026)", "Horaria", DIR_LANDING, "Lote automatizado"),
    ("Hoja Resumen", "Totales mensuales por subestación", "xlsx", "171 celdas", "Mensual", DIR_LANDING, "Lote (conciliación)"),
    ("Medidor general (demanda)", "Energía total comprada campus Medellín", "xlsx (ancho)", "9.480 horas (1-ago-2025 a 30-ago-2026)", "Horaria", DIR_LANDING, "Lote aplanado en Bronze"),
    ("Clima NASA POWER", "Reanálisis meteorológico (5 variables)", "API REST / CSV", "13.896 horas (1-abr-2025 a 31-oct-2026)", "Horaria", "API + landing/", "API con plan B en archivo"),
    ("Catálogo subestaciones", "Inventario técnico transformadores", "xlsx", "12 filas (11 Medellín)", "Estático", DIR_LANDING, "Lote"),
    ("Paneles y diésel", "Inventario generación distribuida y respaldo", "xlsx", "3 plantas y 9 generadores", "Estático", DIR_LANDING, "Lote"),
    ("Calendario académico", "Periodos oficiales EAFIT", "csv", "23 periodos", "Semestral", DIR_LANDING, "Lote")
]
df_cat_fuentes = spark.createDataFrame(
    catalogo_fuentes,
    "fuente string, tipo string, formato string, volumen string, frecuencia string, ubicacion string, modo_ingesta string"
).withColumn("actualizado_ts", F.current_timestamp())
guardar(df_cat_fuentes, "bronze_catalogo_fuentes")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Consumo por subestación (una hoja `SUB_*` por subestación)

# COMMAND ----------

# Celda 5: Recorrido automático de landing/, validación y cuarentena
archivos = sorted([f for f in os.listdir(DIR_LANDING) if os.path.isfile(f"{DIR_LANDING}/{f}")])
print(f"Archivos detectados en landing/: {archivos}")

for arch in archivos:
    ruta_full = f"{DIR_LANDING}/{arch}"
    low = arch.lower()
    try:
        # 1. Archivo principal de subestaciones + Resumen
        if low.endswith(".xlsx") and ("2025" in low or "subestaciones_15" in low):
            xl = pd.ExcelFile(ruta_full)
            hojas_sub = [h for h in xl.sheet_names if h.upper().startswith("SUB_")]
            if len(hojas_sub) == 0 or "Resumen" not in xl.sheet_names:
                raise ValueError(f"Estructura inválida: faltan hojas SUB_* o Resumen. Hojas: {xl.sheet_names}")
            
            partes = []
            for h in hojas_sub:
                d = pd.read_excel(xl, sheet_name=h, header=None, dtype=str)
                if d.shape[1] < 3:
                    raise ValueError(f"Hoja {h} tiene menos de 3 columnas")
                enc_kwh = d.iloc[0, 2]
                d = d.iloc[1:, :3].copy()
                d.columns = ["fecha_raw", "hora_raw", "kwh_raw"]
                d["fila_origen"] = (d.index + 1).astype(str)
                d = d[~vacia(d[["fecha_raw", "hora_raw", "kwh_raw"]])]
                d["subestacion"] = h.replace("SUB_", "")
                d["hoja_origen"] = h
                d["encabezado_kwh"] = enc_kwh
                partes.append(d)
            
            cons_pdf = pd.concat(partes, ignore_index=True)
            cons_pdf["archivo_origen"] = arch
            cols = ["subestacion", "hoja_origen", "fila_origen", "fecha_raw", "hora_raw", "kwh_raw", "encabezado_kwh", "archivo_origen"]
            guardar(a_spark_texto(cons_pdf, cols).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_consumo_raw")
            registrar_log("Subestaciones", arch, len(cons_pdf), "OK", f"{len(hojas_sub)} hojas cargadas")

            # Hoja Resumen
            res = pd.read_excel(xl, sheet_name="Resumen", header=None, dtype=str)
            res.columns = [f"c{i}" for i in range(res.shape[1])]
            res["fila_origen"] = (res.index + 1).astype(str)
            res = res[~vacia(res.drop(columns="fila_origen"))]
            res["archivo_origen"] = arch
            guardar(a_spark_texto(res, list(res.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_resumen_raw")
            registrar_log("Hoja Resumen", arch, len(res), "OK", "Resumen mensual cargado")
            mover_archivo(ruta_full, DIR_PROC, arch)

        # 2. Catálogo técnico de subestaciones
        elif low.endswith(".xlsx") and "eafit" in low and "consumo" not in low:
            cat = pd.read_excel(ruta_full, header=None, dtype=str)
            if cat.shape[0] < 3:
                raise ValueError("Catálogo vacío o sin filas de datos")
            nombres = [limpiar_nombre(c) for c in cat.iloc[1]]
            cat = cat.iloc[2:].copy()
            cat.columns = nombres
            cat = cat[~vacia(cat)]
            cat["archivo_origen"] = arch
            guardar(a_spark_texto(cat, list(cat.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_catalogo_raw")
            registrar_log("Catálogo subestaciones", arch, len(cat), "OK", "Catálogo técnico cargado")
            mover_archivo(ruta_full, DIR_PROC, arch)

        # 3. Medidor general (consumo_total.xlsx: demanda, paneles, generadores_diesel)
        elif low.endswith(".xlsx") and "consumo_total" in low:
            xl = pd.ExcelFile(ruta_full)
            if "demanda" not in xl.sheet_names:
                raise ValueError(f"Falta hoja 'demanda'. Hojas encontradas: {xl.sheet_names}")
            raw = pd.read_excel(xl, sheet_name="demanda", header=None, dtype=str)
            filas = []
            for c in raw.columns:
                if c < 3:
                    continue
                fecha = raw.iat[1, c]
                if pd.isna(fecha):
                    continue
                for r in range(2, 27):  # filas 2..25 = horas 0..23; fila 26 = total diario
                    v = raw.iat[r, c]
                    filas.append((str(c), str(fecha), str(raw.iat[r, 2]), None if pd.isna(v) else str(v), arch))
            dem_pdf = pd.DataFrame(filas, columns=["col_origen", "fecha_raw", "hora_raw", "kwh_raw", "archivo_origen"])
            guardar(a_spark_texto(dem_pdf, list(dem_pdf.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_demanda_raw")
            registrar_log("Medidor general (demanda)", arch, len(dem_pdf), "OK", "Hoja ancha aplanada")

            if "paneles" in xl.sheet_names:
                pan = pd.read_excel(xl, sheet_name="paneles", dtype=str)
                pan.columns = [limpiar_nombre(c) for c in pan.columns]
                pan["archivo_origen"] = arch
                guardar(a_spark_texto(pan, list(pan.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_paneles")
                registrar_log("Paneles solares", arch, len(pan), "OK", "Inventario paneles")

            if "generadores_diesel" in xl.sheet_names:
                gen = pd.read_excel(xl, sheet_name="generadores_diesel", dtype=str)
                gen.columns = [limpiar_nombre(c) for c in gen.columns]
                gen["archivo_origen"] = arch
                guardar(a_spark_texto(gen, list(gen.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_generadores")
                registrar_log("Generadores diésel", arch, len(gen), "OK", "Inventario generadores")

            mover_archivo(ruta_full, DIR_PROC, arch)

        # 4. Clima CSV (Plan B local en landing/)
        elif low.endswith(".csv") and "clima" in low:
            cli = pd.read_csv(ruta_full, dtype=str)
            cli.columns = [limpiar_nombre(c) for c in cli.columns]
            cli["archivo_origen"] = arch
            guardar(a_spark_texto(cli, list(cli.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_clima_raw")
            registrar_log("Clima NASA POWER (CSV)", arch, len(cli), "OK", "Cargado desde archivo en landing/")
            mover_archivo(ruta_full, DIR_PROC, arch)

        # 5. Calendario académico oficial
        elif low.endswith(".csv") and "calendario" in low:
            cal = pd.read_csv(ruta_full, dtype=str)
            cal.columns = [limpiar_nombre(c) for c in cal.columns]
            cal["archivo_origen"] = arch
            guardar(a_spark_texto(cal, list(cal.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_calendario_raw")
            registrar_log("Calendario académico", arch, len(cal), "OK", "Calendario oficial cargado")
            mover_archivo(ruta_full, DIR_PROC, arch)

        else:
            raise ValueError("Archivo no reconocido por las reglas de ingesta")

    except Exception as e:
        print(f"ERROR en {arch} -> enviando a cuarentena/: {e}")
        registrar_log("Desconocido/Error", arch, 0, "CUARENTENA", str(e))
        mover_archivo(ruta_full, DIR_CUAR, arch)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Hoja `Resumen` (consumo mensual por subestación — se usará para conciliar)

# COMMAND ----------

# Celda 6: Clima NASA POWER vía API REST con fallback automático (T1.6)
url_nasa = (
    "https://power.larc.nasa.gov/api/temporal/hourly/point"
    "?parameters=T2M,RH2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN"
    "&community=RE&longitude=-75.579&latitude=6.200&start=20250401&end=20261031&format=JSON"
)
try:
    resp = requests.get(url_nasa, timeout=15)
    resp.raise_for_status()
    data_json = resp.json()["properties"]["parameter"]
    df_api = pd.DataFrame(data_json).reset_index().rename(columns={"index": "fecha_hora_nasa"})
    df_api.columns = [limpiar_nombre(c) for c in df_api.columns]
    df_api = df_api.astype(str)
    df_api["archivo_origen"] = "API_NASA_POWER"
    guardar(a_spark_texto(df_api, list(df_api.columns)).withColumn("fecha_ingesta", F.lit(FECHA_INGESTA).cast("timestamp")), "bronze_clima_raw")
    registrar_log("Clima NASA POWER (API)", "API_REST", len(df_api), "OK", "Descarga directa exitosa")
    print("Clima descargado directo desde API NASA POWER.")
except Exception as e:
    msg_err = f"Red restringida o fallo API ({type(e).__name__}: {str(e)[:200]}). Se usa fallback CSV si existe."
    registrar_log("Clima NASA POWER (API)", "API_REST", 0, "FALLBACK_CSV", msg_err)
    print(msg_err)

display(spark.table(T("bronze_ingesta_log")).orderBy(F.desc("ts")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Catálogo de subestaciones (12 filas; Llanogrande se conserva en Bronze pero se excluye en Silver)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Verificación
# MAGIC Cada subestación debería tener 13.344 filas (556 días × 24 h, del 15-abr-2025 al 22-oct-2026).
