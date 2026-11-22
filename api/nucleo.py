"""Lógica del pronóstico, copiada del notebook del modelo (los números de celda son los del notebook).

Diferencias con el notebook (ninguna cambia los cálculos):
  - lo que allá son variables globales (clima, capacidades, tarifa) aquí llega en un objeto ``Contexto``;
  - capacidades, tarifas y subestaciones con reglas especiales se leen de un archivo privado que no se publica;
  - los nombres de las subestaciones salen de las hojas del Excel.
"""
import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

import holidays
import joblib
import numpy as np
import pandas as pd

# El notebook calcula promedios sobre horas sin dato: el resultado correcto es NaN y el aviso no aporta
warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0 for slice", category=RuntimeWarning)

RAIZ = Path(__file__).resolve().parent.parent
RUTAS = {   # variable de entorno -> (descripción, valor por defecto relativo a la raíz del repositorio)
    "RUTA_MODELO": ("el modelo entrenado", "modelo/modelos/modelo_energia_v4.joblib"),
    "RUTA_CONSUMO": ("los datos de consumo", "datos/Subestaciones_15_abril_2025_-_22_oct_2026.xlsx"),
    "RUTA_CLIMA": ("los datos de clima", "datos/clima_nasa_power_eafit.csv"),
    "RUTA_PRIVADO": ("la configuración confidencial", "datos/privado.json"),
}


class ArchivoFaltante(RuntimeError):
    """Falta un archivo (o una clave de privado.json) sin el cual la API no puede arrancar."""


def ruta_de(variable):
    ruta = Path(os.environ.get(variable) or RUTAS[variable][1])
    return ruta if ruta.is_absolute() else RAIZ / ruta


# ---- Celda 2: fin de los datos --------------------------------------------------------------------
FIN_DATOS = pd.Timestamp("2026-10-22 23:00")


# ---- Celda 4: lectura de los datos ----------------------------------------------------------------
def cargar_subestaciones(ruta):
    # Lee el Excel (una hoja por subestación) y devuelve una tabla larga: timestamp | subestacion | kwh
    hojas = pd.read_excel(ruta, sheet_name=None)          # dict {nombre_hoja: DataFrame}
    partes = []
    for nombre, df in hojas.items():
        if nombre == "Resumen":                            # la hoja resumen no es una serie
            continue
        df = df.iloc[:, :3].copy()
        df.columns = ["fecha", "hora", "kwh"]
        ts = pd.to_datetime(df["fecha"]).dt.normalize() + pd.to_timedelta(df["hora"].astype(str))
        partes.append(pd.DataFrame({"timestamp": ts, "subestacion": nombre.replace("SUB_", ""),
                                    "kwh": pd.to_numeric(df["kwh"], errors="coerce")}))
    return pd.concat(partes, ignore_index=True)


def ancho_desde_largo(largo):
    # Tabla ancha horaria (filas = horas, columnas = subestaciones) con la malla horaria completa hasta FIN_DATOS
    ancho = largo.pivot(index="timestamp", columns="subestacion", values="kwh")
    ancho = ancho.reindex(pd.date_range(ancho.index.min(), FIN_DATOS, freq="h", name="timestamp"))
    ancho.index.freq = None
    ancho.columns.name = "subestacion"
    return ancho[sorted(ancho.columns)]


def cargar_clima(ruta):
    clima = pd.read_csv(ruta, parse_dates=["timestamp"]).set_index("timestamp")
    return clima.mask(clima <= -999)                 # -999 = dato faltante en NASA POWER


# ---- Celda 21: clima diario -----------------------------------------------------------------------
def clima_diario(clima):
    clima_d = clima.resample("D").agg({"T2M": ["mean", "max"], "PRECTOTCORR": "sum", "ALLSKY_SFC_SW_DWN": "sum"})
    clima_d.columns = ["t_media", "t_max", "precip", "radiacion"]
    return clima_d


# ---- Celda 15: calendario -------------------------------------------------------------------------
FESTIVOS = holidays.Colombia(years=[2025, 2026, 2027])

# Periodos académicos (Sección 1.4, supuesto S8). Las filas posteriores sobrescriben a las anteriores,
# por eso los recesos van después de su semestre.
# 2026: cronograma OFICIAL de pregrado (aprobado por el Consejo Académico el 22-abr-2026, con el ajuste de 2026-2).
# 2025: ESTIMADO trasladando 2026 exactamente 52 semanas (mismo día de la semana) y comprobado con el consumo;
#       Semana Santa con sus fechas reales y el cierre 2025 según la caída del consumo (desde el 20-dic).
# "clases" incluye las evaluaciones finales. Fuente común del proyecto: ref_calendario_academico.csv
PERIODOS = [
    ("2025-01-20", "2025-05-31", "clases"), ("2025-04-14", "2025-04-20", "receso"),          # 2025-1 (estimado) + Semana Santa real
    ("2025-06-01", "2025-07-13", "intersemestral"),
    ("2025-07-14", "2025-11-22", "clases"), ("2025-10-06", "2025-10-12", "receso"),          # 2025-2 (estimado, confirmado con el consumo)
    ("2025-11-23", "2025-12-19", "intersemestral"), ("2025-12-20", "2026-01-12", "cierre"),
    ("2026-01-13", "2026-01-18", "intersemestral"),
    ("2026-01-19", "2026-05-30", "clases"), ("2026-03-30", "2026-04-05", "receso"),          # 2026-1 oficial + Semana Santa
    ("2026-05-31", "2026-07-12", "intersemestral"),
    ("2026-07-13", "2026-11-21", "clases"), ("2026-10-05", "2026-10-11", "receso"),          # 2026-2 oficial
    ("2026-11-22", "2026-12-13", "intersemestral"), ("2026-12-14", "2027-01-11", "cierre"),
]
INICIO_SEMESTRE = pd.to_datetime(["2025-01-20", "2025-07-14", "2026-01-19", "2026-07-13"])

def calendario_diario(dias):
    # Devuelve, para cada día, todo lo que se sabe de él con anticipación (no hay fuga: es calendario).
    d = pd.DataFrame(index=pd.DatetimeIndex(dias).normalize())
    d["dia_semana"] = d.index.dayofweek
    d["festivo"] = [int(x.date() in FESTIVOS) for x in d.index]
    d["fin_semana"] = (d.dia_semana >= 5).astype(int)
    d["laboral"] = ((d.festivo == 0) & (d.fin_semana == 0)).astype(int)
    d["periodo"] = "intersemestral"
    for ini, fin, etiqueta in PERIODOS:
        d.loc[(d.index >= ini) & (d.index <= fin), "periodo"] = etiqueta
    # semana del semestre: 1 = primera semana de clases del semestre en curso
    d["semana_semestre"] = [((x - INICIO_SEMESTRE[INICIO_SEMESTRE <= x].max()).days // 7 + 1) for x in d.index]
    # puente: día no laboral dentro de un bloque de ≥3 días no laborales seguidos (fin de semana largo).
    # OJO: la racha se calcula con TODOS los días que se le pasen; por eso el calendario se calcula UNA sola vez
    # (CAL, abajo) y se reutiliza igual en entrenamiento, validación y pronóstico. No debe recortarse por fecha.
    nl = (d.laboral == 0).astype(int)
    racha = nl.groupby((nl != nl.shift()).cumsum()).transform("sum")
    d["puente"] = ((nl == 1) & (racha >= 3)).astype(int)
    return d

# CAL cubre hasta fin de 2026: festivos y periodos se conocen con meses de anticipación. Para pronosticar más allá
# habría que ampliar este rango (y PERIODOS / INICIO_SEMESTRE).
dias = pd.date_range("2025-04-01", "2026-12-31", freq="D")
CAL = calendario_diario(dias)


# ---- Datos cargados al arrancar (en el notebook son variables globales) -----------------------------
@dataclass(frozen=True)
class Contexto:
    artefacto: dict              # contenido del .joblib
    crudo: pd.DataFrame          # consumo horario original (filas = horas, columnas = subestaciones)
    limpio: pd.DataFrame         # el mismo consumo después de la limpieza causal
    clima: pd.DataFrame
    clima_d: pd.DataFrame
    subestaciones: list          # nombres tomados de las hojas del Excel
    capacidad: dict              # kVA por subestación (privado.json)
    tarifas: dict                # COP/kWh por mes "AAAA-MM" (privado.json)
    reglas: dict                 # subestaciones con reglas especiales de limpieza (privado.json)

    @property
    def ultimo_dato(self):
        return self.crudo.dropna(how="all").index.max()

    def tarifa(self, ts):        # celda 80
        return ts.dt.strftime("%Y-%m").map(self.tarifas)


def cargar_contexto():
    """Carga modelo, datos y configuración. Se llama una sola vez, al arrancar la API."""
    for variable, (que, _) in RUTAS.items():
        if not ruta_de(variable).is_file():
            raise ArchivoFaltante(
                f"La API no puede arrancar: falta {que} ({ruta_de(variable)}). Ese archivo no se publica y solo lo "
                f"tiene el equipo del proyecto. Cópielo en esa ruta o indique otra con la variable {variable}.")
    privado = json.loads(ruta_de("RUTA_PRIVADO").read_text(encoding="utf-8"))
    for clave in ("capacidad_kva", "tarifa_cop_kwh", "reglas_limpieza"):
        if clave not in privado:
            raise ArchivoFaltante(f"La API no puede arrancar: a {ruta_de('RUTA_PRIVADO')} le falta la clave '{clave}'. "
                                  "La versión completa de ese archivo solo la tiene el equipo del proyecto.")
    crudo = ancho_desde_largo(cargar_subestaciones(ruta_de("RUTA_CONSUMO")))
    clima = cargar_clima(ruta_de("RUTA_CLIMA"))
    capacidad, reglas = privado["capacidad_kva"], privado["reglas_limpieza"]
    return Contexto(artefacto=joblib.load(ruta_de("RUTA_MODELO")), crudo=crudo,
                    limpio=limpiar_series(crudo, capacidad, reglas)[0], clima=clima, clima_d=clima_diario(clima),
                    subestaciones=list(crudo.columns), capacidad=capacidad, tarifas=privado["tarifa_cop_kwh"],
                    reglas=reglas)


# ---- Celda 24: limpieza causal R1-R4 ---------------------------------------------------------------
FECHA_CAMBIO_EQUIPO = pd.Timestamp("2026-03-05")
MIN_HORAS_HISTORIA_R3 = 24 * 30      # R3 no se evalúa hasta tener 30 días de historia válida de esa serie
FACTOR_P99_R3 = 1.5                  # R3 exige superar 1,5 x el percentil 99 del histórico. (Con 1,0 x la regla confundía
                                     # con "pico" el primer valor alto de cada subida normal de la mañana: ver 4.1b)


def limpiar_series(ancho, capacidad, reglas):
    # Devuelve (series_limpias, banderas). 'banderas' dice por qué se borró cada valor.
    # LIMPIEZA CAUSAL: el estado de la hora t depende solo de las horas <= t (ninguna regla mira al futuro).
    cambio = reglas["subestacion_cambio_equipo"]
    sin_r3 = reglas["subestaciones_sin_regla_pico"]
    limpio = ancho.copy()
    banderas = pd.DataFrame("ok", index=ancho.index, columns=ancho.columns)
    banderas[ancho.isna()] = "nulo_original"

    # R1: ceros y negativos
    m = limpio <= 0
    banderas[m] = "R1_cero_o_negativo"; limpio = limpio.mask(m)

    # R2: supera la capacidad física del transformador
    for k in limpio.columns:
        aplica = (limpio.index < FECHA_CAMBIO_EQUIPO) if k == cambio else np.ones(len(limpio), bool)
        m = aplica & (limpio[k] > capacidad[k]).values
        banderas.loc[m, k] = "R2_supera_capacidad"; limpio.loc[m, k] = np.nan

    # R4: régimen anterior de la subestación que cambió de equipo (va ANTES de R3 para que el percentil histórico no mezcle regímenes)
    antes = limpio.index < FECHA_CAMBIO_EQUIPO
    m = antes & limpio[cambio].notna().values
    banderas.loc[m, cambio] = "R4_regimen_anterior"; limpio.loc[antes, cambio] = np.nan

    # R3: pico aislado, versión CAUSAL (solo mira horas anteriores; no se aplica a las de "sin_regla_pico")
    for k in limpio.columns:
        if k in sin_r3:
            continue
        s = limpio[k]
        # mediana de las 6 horas ANTERIORES (shift(1) = hasta t-1); se exigen >= 3 con dato
        previas = s.shift(1).rolling(6, min_periods=3).median()
        # percentil 99 del HISTÓRICO anterior (hasta t-1); se exigen >= 30 días (720 h) con dato
        p99_hist = s.expanding(min_periods=MIN_HORAS_HISTORIA_R3).quantile(0.99).shift(1)
        m = ((s > 2 * previas) & (s > FACTOR_P99_R3 * p99_hist)).values   # si falta referencia el resultado es False: no se descarta
        banderas.loc[m, k] = "R3_pico_aislado"; limpio.loc[m, k] = np.nan
    return limpio, banderas


# ---- Celda 37: tabla supervisada, una fila por (subestación, hora) ----------------------------------
def construir_tabla(limpio, ctx, hasta=None):
    # limpio: tabla ancha (filas = horas, columnas = subestaciones), SIN datos posteriores a la emisión.
    # hasta:  si se da (p. ej. '2026-10-23 23:00'), se agregan horas vacías para pronosticar ese futuro.
    if hasta is not None:
        limpio = limpio.reindex(pd.date_range(limpio.index.min(), pd.Timestamp(hasta), freq="h"))
    dias = pd.date_range(limpio.index.min().normalize(), limpio.index.max().normalize(), freq="D")
    # CALENDARIO FIJO: se lee de la tabla global CAL (festivos y periodos se conocen con meses de anticipación).
    # Antes se recalculaba solo con los días presentes: al cortar el calendario en el día pronosticado, un sábado
    # de puente "no veía" el lunes festivo y `puente` valía 0 en producción pero 1 en el entrenamiento.
    cal = CAL.reindex(dias)
    assert cal.laboral.notna().all(), "CAL no cubre estas fechas: amplíe el rango de CAL (sección 3.5)"

    # Para cada día: último día anterior del MISMO tipo (laboral / no laboral)
    ultimo, prev_mismo = {0: None, 1: None}, {}
    for dia_i, es_lab in cal.laboral.items():
        prev_mismo[dia_i] = ultimo[es_lab]; ultimo[es_lab] = dia_i
    prev_mismo = pd.Series(prev_mismo)

    partes = []
    for k in limpio.columns:
        s = limpio[k]
        # Matriz día × hora (24 columnas) para calcular agregados por día de forma sencilla
        M = pd.DataFrame(s.values.reshape(-1, 24), index=dias)
        df = pd.DataFrame({"timestamp": s.index, "subestacion": k, "kwh": s.values})
        dia = df.timestamp.dt.normalize(); hora = df.timestamp.dt.hour.values

        # --- REZAGOS (lags): la misma hora, hace 24 / 48 / 168 / 336 horas (1 día, 2 días, 1 semana, 2 semanas) ---
        # s.shift(L) con L POSITIVO desplaza la serie hacia adelante: la fila de la hora t recibe el valor de la hora t-L,
        # es decir, del PASADO. Como L >= 24 h, a las 00:00 de D todos los rezagos de las 24 horas de D ya existen.
        # (Un shift negativo, o L < 24, usaría el futuro o horas del mismo día que aún no ocurren: por eso no se usan.)
        for L in (24, 48, 168, 336):
            df[f"lag_{L}"] = s.shift(L).values
        # --- Rezago del último día del mismo tipo ---
        idx_prev = dia.map(prev_mismo)
        Mlarga = M.stack(future_stack=True)
        df["lag_mismo_tipo"] = Mlarga.reindex(pd.MultiIndex.from_arrays([idx_prev, hora])).values
        df["dias_desde_mismo_tipo"] = (dia - idx_prev).dt.days.values

        # --- Nivel del día anterior (todas las horas de la fila comparten estos valores) ---
        horas_validas = M.notna().sum(axis=1)
        total = (M.mean(axis=1) * 24).where(horas_validas >= 20)          # total del día si hay ≥ 20 horas
        ayer = lambda serie: serie.shift(1).reindex(dia).values          # valor del día D-1 para cada fila de D
        df["total_ayer"] = ayer(total)
        df["max_ayer"] = ayer(M.max(axis=1))
        df["prom_ult3h_ayer"] = ayer(M[[21, 22, 23]].mean(axis=1))
        # nivel_7d: kWh/h promedio de los 7 días anteriores (necesita ≥ 72 horas con dato)
        suma7 = M.sum(axis=1).rolling(7, min_periods=1).sum().shift(1)
        n7 = M.notna().sum(axis=1).rolling(7, min_periods=1).sum().shift(1)
        df["nivel_7d"] = (suma7 / n7).where(n7 >= 72).reindex(dia).values

        # --- Estadísticas de la misma hora en días anteriores ---
        mismas7 = np.column_stack([s.shift(24 * i).values for i in range(1, 8)])
        mismas28 = np.column_stack([s.shift(24 * i).values for i in range(1, 29)])
        with np.errstate(all="ignore"):
            df["media_7d_misma_hora"] = np.nanmean(mismas7, axis=1)
            df["std_4sem_misma_hora"] = np.nanstd(mismas28, axis=1)
        df["razon_24_168"] = (df.lag_24 / df.lag_168).replace([np.inf, -np.inf], np.nan).clip(0, 5)
        partes.append(df)

    T = pd.concat(partes, ignore_index=True)
    dia = T.timestamp.dt.normalize()
    # --- Calendario de la fila (se conoce con anticipación) ---
    T["hora"] = T.timestamp.dt.hour
    T["hora_sin"], T["hora_cos"] = np.sin(2 * np.pi * T.hora / 24), np.cos(2 * np.pi * T.hora / 24)
    for col in ["dia_semana", "festivo", "fin_semana", "laboral", "puente", "periodo", "semana_semestre"]:
        T[col] = dia.map(CAL[col]).values                        # mismo calendario en entrenamiento, validación y pronóstico
    T["dow_sin"], T["dow_cos"] = np.sin(2 * np.pi * T.dia_semana / 7), np.cos(2 * np.pi * T.dia_semana / 7)
    T["mes"] = T.timestamp.dt.month
    T["ayer_laboral"] = dia.map(CAL.laboral.shift(1)).values        # tipo del día D-1 (calendario fijo)
    T["hace7_laboral"] = dia.map(CAL.laboral.shift(7)).values       # tipo del día D-7
    # --- Clima observado de AYER (válido) ---
    for c in ["t_media", "t_max", "precip"]:
        T[f"{c}_ayer"] = dia.map(ctx.clima_d[c].shift(1)).values
    # --- Clima observado del MISMO día: ORÁCULO, solo para el experimento de la Sección 7 ---
    T["temp_hora_obs"] = T.timestamp.map(ctx.clima.T2M).values
    T["t_max_dia_obs"] = dia.map(ctx.clima_d.t_max).values
    # --- Tipos categóricos con categorías fijas (iguales en entrenamiento y pronóstico) ---
    T["subestacion"] = pd.Categorical(T.subestacion, categories=ctx.subestaciones)
    T["periodo"] = pd.Categorical(T.periodo, categories=["clases", "receso", "intersemestral", "cierre"])
    return T


# ---- Celda 39: variables finales y normalización ----------------------------------------------------
VARS_CONSUMO = ["lag_24", "lag_48", "lag_168", "lag_336", "lag_mismo_tipo", "max_ayer", "prom_ult3h_ayer",
                "media_7d_misma_hora", "std_4sem_misma_hora"]
VARS_CAL = ["hora", "hora_sin", "hora_cos", "dia_semana", "dow_sin", "dow_cos", "mes", "festivo", "fin_semana",
            "laboral", "puente", "ayer_laboral", "hace7_laboral", "dias_desde_mismo_tipo", "semana_semestre"]
VARS_CLIMA_AYER = ["t_media_ayer", "t_max_ayer", "precip_ayer"]
VARS_ORACULO = ["temp_hora_obs", "t_max_dia_obs"]
CATEGORICAS = ["subestacion", "periodo"]

def preparar(T):
    # Agrega las versiones relativas (÷ nivel_7d). No usa información futura: nivel_7d es del pasado.
    T = T.copy()
    for c in VARS_CONSUMO:
        T[c + "_rel"] = T[c] / T.nivel_7d
    T["total_ayer_rel"] = T.total_ayer / (24 * T.nivel_7d)
    T["y_rel"] = T.kwh / T.nivel_7d
    return T

VARS_BASE = [c + "_rel" for c in VARS_CONSUMO] + ["total_ayer_rel", "razon_24_168", "nivel_7d"] + VARS_CAL + CATEGORICAS


# ---- Celda 41: todo el flujo desde el dato crudo ----------------------------------------------------
def tabla_desde_crudo(crudo, ctx, hasta=None):
    # Todo el flujo desde el dato crudo: limpiar -> tabla de variables -> versiones relativas.
    lim, _ = limpiar_series(crudo, ctx.capacidad, ctx.reglas)
    return preparar(construir_tabla(lim, ctx, hasta=hasta))


# ---- Celda 92: predicción con el modelo guardado ----------------------------------------------------
def predecir_con_artefacto(art, filas):
    # Reproduce ModeloEnergia.predict sin depender de las clases del notebook (así lo usa la API)
    preds = []
    for comp in art["componentes"]:
        X = filas[comp["variables"]].copy()
        if comp["tipo"] == "ridge":
            X["sub_hora_lab"] = (filas.subestacion.astype(str) + "_" + filas.hora.astype(str) + "_"
                                 + filas.laboral.astype(str))
        for c, cats in art["categorias"].items():
            if c in X:
                X[c] = pd.Categorical(X[c], categories=cats)
                if comp["tipo"] == "rf":
                    X[c] = X[c].cat.codes
        p = comp["estimador"].predict(X)
        if comp["relativo"]:
            p = p * filas.nivel_7d.values
        preds.append(np.clip(p, 0, None))
    return np.mean(preds, axis=0)


# ---- Celda 90: pronóstico de un día -----------------------------------------------------------------
class FechaNoValida(ValueError):
    """No se puede pronosticar esa fecha con los datos disponibles."""


def validar_fecha(fecha, ctx):
    fecha = pd.Timestamp(fecha)
    primera = ctx.crudo.index.min().normalize() + pd.Timedelta(days=1)
    ultima = ctx.ultimo_dato.normalize() + pd.Timedelta(days=1)
    if fecha > ultima:
        raise FechaNoValida(f"La fecha {fecha.date()} es posterior al último día que se puede pronosticar "
                            f"({ultima.date()}): el último dato es de {ctx.ultimo_dato:%Y-%m-%d %H:%M}.")
    if fecha < primera:
        raise FechaNoValida(f"No hay historia suficiente para pronosticar {fecha.date()}: los datos empiezan el "
                            f"{ctx.crudo.index.min():%Y-%m-%d}.")


def pronosticar_dia(fecha, ctx):
    umbral = ctx.artefacto["umbral_alerta_relativo"]            # banda elegida en validación
    validar_fecha(fecha, ctx)
    # 1) Corta los DATOS CRUDOS a la medianoche: nada del día 'fecha' en adelante se usa
    fecha = pd.Timestamp(fecha)
    ultima_hora = fecha - pd.Timedelta(hours=1)               # <- CORTE DE LA EMISIÓN (23:00 del día anterior)
    # 2) Limpia (causal) y construye las 24 filas de cada subestación con las mismas pistas del entrenamiento
    T = tabla_desde_crudo(ctx.crudo.loc[:ultima_hora], ctx, hasta=fecha + pd.Timedelta(hours=23))
    filas = T[T.timestamp.dt.normalize() == fecha].copy()
    assert filas[["subestacion", "timestamp"]].drop_duplicates().shape[0] == 216, "se esperaban 9 subestaciones × 24 horas"
    sin_historia = sorted(filas.loc[filas.nivel_7d.isna(), "subestacion"].astype(str).unique())
    if sin_historia:
        raise FechaNoValida(f"No hay historia suficiente para pronosticar {fecha.date()}: se necesitan al menos 72 horas "
                            "válidas en los 7 días anteriores y no las tienen: " + ", ".join(sin_historia) + ".")
    # 3) Predice y agrega banda de alerta y costo
    filas["pronostico_kwh"] = predecir_con_artefacto(ctx.artefacto, filas)
    filas["banda_alerta_kwh"] = filas.pronostico_kwh + filas.subestacion.map(umbral).astype(float) * filas.nivel_7d
    filas["costo_cop"] = filas.pronostico_kwh * ctx.tarifa(filas.timestamp)
    return filas[["timestamp", "subestacion", "pronostico_kwh", "banda_alerta_kwh", "costo_cop", "lag_168", "nivel_7d"]]
