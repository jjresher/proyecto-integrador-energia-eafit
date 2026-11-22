"""De dónde lee la API sus datos: Databricks (tablas Silver y Gold vía SQL Warehouse) o, como respaldo, archivos locales.

FUENTE_DATOS = "auto"       -> usa Databricks si hay credenciales y responde; si no, los archivos locales (por defecto).
FUENTE_DATOS = "databricks" -> obliga a usar Databricks; si no puede, la API no arranca y explica por qué.
FUENTE_DATOS = "local"      -> solo archivos locales (sirve para la demo sin internet).

Credenciales (nunca en el código): variables de entorno o un archivo .env en la raíz del repositorio o en api/.
  DATABRICKS_HOST (o DATABRICKS_SERVER_HOSTNAME), DATABRICKS_HTTP_PATH y DATABRICKS_TOKEN.
  Dentro de una Databricks App no hace falta token: basta DATABRICKS_WAREHOUSE_ID (usa la identidad de la App).
"""
import os
from pathlib import Path

import pandas as pd

CATALOGO, ESQUEMA = "workspace", "eafit_energia"
TABLAS = {   # cambiar aquí si el pipeline usa otros nombres
    "consumo": "silver_consumo",
    "clima": "silver_clima",
    "emitido": "gold_pronostico_emitido",
}
# Nombres de columna aceptados (los mismos de la celda 2.1 del notebook); se toma el primero que exista
COLUMNAS = {
    "timestamp": ["ts", "timestamp", "fecha_hora", "fecha_hora_local"],
    "fecha": ["fecha", "dia"],
    "hora": ["hora"],
    "subestacion": ["subestacion", "codigo_subestacion", "id_subestacion"],
    "kwh_original": ["kwh_original", "kwh_crudo", "kwh_raw", "valor_original"],   # nunca la columna limpia
    "T2M": ["T2M", "temperatura", "temperatura_c", "temp_c"],
    "PRECTOTCORR": ["PRECTOTCORR", "precipitacion", "precipitacion_mm", "precip_mm"],
    "ALLSKY_SFC_SW_DWN": ["ALLSKY_SFC_SW_DWN", "radiacion", "radiacion_ghi", "ghi"],
}
ZONA = "America/Bogota"


class SinDatabricks(RuntimeError):
    """No hay credenciales de Databricks o no se pudo leer de allá."""


# ---- Credenciales -----------------------------------------------------------------------------------
def cargar_env(raiz):
    # Lee líneas CLAVE=valor de .env (raíz del repositorio y api/) sin pisar variables ya definidas
    for ruta in (Path(raiz) / ".env", Path(__file__).resolve().parent / ".env"):
        if ruta.is_file():
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                if "=" in linea and not linea.lstrip().startswith("#"):
                    k, v = linea.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def modo():
    valor = os.environ.get("FUENTE_DATOS", "auto").strip().lower()
    if valor not in ("auto", "databricks", "local"):
        raise ValueError(f"FUENTE_DATOS debe ser 'auto', 'databricks' o 'local' (llegó {valor!r})")
    return valor


def hay_credenciales():
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token_ok = host and os.environ.get("DATABRICKS_HTTP_PATH") and os.environ.get("DATABRICKS_TOKEN")
    return bool(token_ok or os.environ.get("DATABRICKS_WAREHOUSE_ID"))


def conectar():
    # Igual que la API original del equipo: token desde .env, o identidad de la Databricks App
    try:
        from databricks import sql                 # pip install databricks-sql-connector
    except ImportError as e:
        raise SinDatabricks("falta la librería databricks-sql-connector (pip install -r api/requirements.txt)") from e
    token = os.environ.get("DATABRICKS_TOKEN")
    if token:
        host = (os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME") or "")
        return sql.connect(server_hostname=host.replace("https://", "").rstrip("/"),
                           http_path=os.environ["DATABRICKS_HTTP_PATH"], access_token=token)
    from databricks.sdk.core import Config         # dentro de una Databricks App
    cfg = Config()
    return sql.connect(server_hostname=cfg.host.replace("https://", ""),
                       http_path="/sql/1.0/warehouses/" + os.environ["DATABRICKS_WAREHOUSE_ID"],
                       credentials_provider=lambda: cfg.authenticate)


def consultar(tabla):
    """Devuelve una tabla completa de Databricks como DataFrame de pandas."""
    with conectar() as conexion:
        with conexion.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {CATALOGO}.{ESQUEMA}.{tabla}")
            return cursor.fetchall_arrow().to_pandas()


# ---- Columnas y horas -------------------------------------------------------------------------------
def columna(df, clave, obligatoria=True):
    por_nombre = {str(c).lower(): c for c in df.columns}
    for candidata in COLUMNAS[clave]:
        if candidata.lower() in por_nombre:
            return por_nombre[candidata.lower()]
    if obligatoria:
        raise KeyError(f"No encuentro la columna '{clave}' (probé {COLUMNAS[clave]}). Columnas: {list(df.columns)}. "
                       "Agregar el nombre correcto en COLUMNAS (api/fuentes.py).")
    return None


def marca_de_tiempo(df):
    # Marca horaria tal como llega (con o sin zona horaria); se pasa a hora local en hora_local()
    c = columna(df, "timestamp", obligatoria=False)
    if c is not None:
        return pd.to_datetime(df[c])
    hora = df[columna(df, "hora")]
    n = pd.to_numeric(hora, errors="coerce")
    if n.notna().all():
        delta = pd.to_timedelta(n, unit="h")
    else:
        texto = hora.astype(str).str.strip()
        delta = pd.to_timedelta(texto.where(texto.str.count(":") != 1, texto + ":00"))
    return pd.to_datetime(df[columna(df, "fecha")]).dt.normalize() + delta


def hora_local(ts, ultima_hora_esperada=23):
    """Convierte marcas con zona a hora de reloj de Medellín, sin zona.

    El conector entrega TIMESTAMP en UTC. Según cómo se escribió cada tabla, la hora de reloj puede estar guardada
    tal cual (00:00 local -> 00:00 UTC) o convertida (00:00 local -> 05:00 UTC). Se elige la lectura en la que el
    último dato cae a las 23:00, que es como terminan todas las series del proyecto (cortes diarios).
    """
    ts = pd.Series(pd.to_datetime(ts))
    if ts.dt.tz is None:
        return ts.astype("datetime64[ns]")
    como_utc = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    como_local = ts.dt.tz_convert(ZONA).dt.tz_localize(None)
    if como_local.max().hour == ultima_hora_esperada and como_utc.max().hour != ultima_hora_esperada:
        return como_local.astype("datetime64[ns]")
    return como_utc.astype("datetime64[ns]")


def codigo_subestacion(s):
    # 'SUB_xxx', 'sub xxx' o 'xxx' -> 'xxx'
    return s.astype(str).str.strip().str.upper().str.replace(r"^SUB[_\s-]*", "", regex=True)


# ---- Lectura desde Databricks -----------------------------------------------------------------------
def consumo_databricks(subestaciones, fin_datos):
    """Consumo horario ORIGINAL de Silver, en tabla larga: timestamp | subestacion | kwh."""
    df = consultar(TABLAS["consumo"])
    largo = pd.DataFrame({"timestamp": hora_local(marca_de_tiempo(df)).values,
                          "subestacion": codigo_subestacion(df[columna(df, "subestacion")]).values,
                          "kwh": pd.to_numeric(df[columna(df, "kwh_original")], errors="coerce").values})
    faltan = sorted(set(subestaciones) - set(largo.subestacion))
    if faltan:
        raise ValueError(f"{TABLAS['consumo']} no trae {len(faltan)} de las subestaciones del modelo.")
    largo = largo[largo.subestacion.isin(subestaciones) & (largo.timestamp <= fin_datos)]
    if (largo.timestamp.dt.minute != 0).any():
        raise ValueError("Hay marcas de tiempo que no caen en la hora exacta: revisar la columna de tiempo.")
    largo = largo.drop_duplicates(["timestamp", "subestacion", "kwh"])
    if largo.duplicated(["timestamp", "subestacion"]).any():
        raise ValueError(f"{TABLAS['consumo']} repite (subestación, hora) con valores distintos.")
    return largo


def clima_databricks():
    df = consultar(TABLAS["clima"])
    clima = pd.DataFrame(index=pd.DatetimeIndex(hora_local(marca_de_tiempo(df)), name="timestamp"))
    for c in ("T2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN"):
        clima[c] = pd.to_numeric(df[columna(df, c)], errors="coerce").values
    clima = clima[~clima.reset_index().duplicated().values].sort_index()
    if clima.index.duplicated().any():
        raise ValueError(f"{TABLAS['clima']} repite horas con valores distintos.")
    return clima


def emitido_databricks():
    """Pronóstico que el Job guardó en Gold (lo escribe 04_resultados)."""
    return normalizar_emitido(consultar(TABLAS["emitido"]))


def normalizar_emitido(df):
    df = df.copy()
    df["timestamp"] = hora_local(df["timestamp"]).values
    df["subestacion"] = codigo_subestacion(df["subestacion"]).values
    for c in ("pronostico_kwh", "banda_alerta_kwh", "costo_cop"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["timestamp", "subestacion", "pronostico_kwh", "banda_alerta_kwh", "costo_cop"]] \
        .sort_values(["subestacion", "timestamp"]).reset_index(drop=True)
