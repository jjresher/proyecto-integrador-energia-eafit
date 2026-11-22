"""API del pronóstico horario de energía. Correr desde la raíz del repositorio: uvicorn api.main:app --reload

Une las dos APIs del equipo:
  - la que consume desde Databricks el pronóstico que el Job guardó en Gold (/pronostico?subestacion=, /recargar,
    /modelo/variables, /predecir), y
  - la que calcula con el modelo el pronóstico de cualquier día (POST /pronostico) a partir del consumo original de
    Silver, con respaldo en archivos locales para trabajar sin conexión.
"""
import datetime as dt
import html
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from api import fuentes, nucleo

# Archivos de Swagger UI guardados en el repositorio: /docs funciona sin internet (p. ej. en la sustentación)
DOCS_ESTATICOS = Path(__file__).resolve().parent / "docs_estaticos"
swagger_ui_path = DOCS_ESTATICOS if (DOCS_ESTATICOS / "swagger-ui-bundle.js").is_file() else None

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Modelo y datos se cargan al arrancar; si falta un archivo o Databricks es obligatorio y no responde, no arranca
    try:
        app.state.ctx = nucleo.cargar_contexto()
    except (nucleo.ArchivoFaltante, fuentes.SinDatabricks) as e:
        log.error("%s", e)
        raise
    log.info("Fuente de datos: %s", app.state.ctx.fuente)
    yield


app = FastAPI(
    title="API de pronóstico de energía · EAFIT",
    version="2.0.0",
    lifespan=ciclo_de_vida,
    docs_url=None if swagger_ui_path else "/docs",
    redoc_url=None,
    description="Pronóstico **horario** de la energía comprada a la red por las 9 subestaciones del campus, emitido a "
                "las 00:00 de cada día solo con datos hasta las 23:00 del día anterior.\n\n"
                "- **Calcular** (`POST /pronostico`): el modelo pronostica el día que se le pida, leyendo el consumo "
                "original de Silver en Databricks.\n"
                "- **Consultar** (`GET /pronostico`): el pronóstico que el Job ya guardó en la tabla Gold.\n\n"
                "Proyecto integrador · SI7006, despliegue de modelos y consumo.",
    openapi_tags=[{"name": "Estado", "description": "Disponibilidad de la API, origen de los datos y ficha del modelo."},
                  {"name": "Pronóstico", "description": "Calcular con el modelo el pronóstico de un día."},
                  {"name": "Resultados en Gold", "description": "Consultar el pronóstico que el Job guardó en Databricks."},
                  {"name": "Modelo", "description": "Usar el modelo directamente con variables ya calculadas."}],
)


if swagger_ui_path:
    app.mount("/archivos-docs", StaticFiles(directory=swagger_ui_path), name="archivos-docs")

    @app.get("/docs", include_in_schema=False)
    def documentacion():
        return get_swagger_ui_html(openapi_url=app.openapi_url, title=app.title + " · documentación",
                                   swagger_js_url="/archivos-docs/swagger-ui-bundle.js",
                                   swagger_css_url="/archivos-docs/swagger-ui.css",
                                   swagger_favicon_url="/archivos-docs/favicon-32x32.png")


@app.get("/", include_in_schema=False)
def inicio():
    return RedirectResponse(url="/docs")


# ---- Esquemas ---------------------------------------------------------------------------------------
class Salud(BaseModel):
    estado: str = Field(description="«ok» si la API está lista", examples=["ok"])
    version_modelo: str = Field(description="Versión del modelo cargado", examples=["v4"])
    modelo: str = Field(description="Modelo elegido en la validación")
    ultimo_dato: dt.datetime = Field(description="Última hora con consumo medido")
    pronosticos_emitidos_cargados: int = Field(description="Filas del pronóstico emitido (Gold) disponibles")
    fuente_datos: dict[str, str] = Field(description="De dónde salió cada dato: Databricks o respaldo local")


class FichaModelo(BaseModel):
    version: str = Field(description="Versión del modelo")
    tipo_modelo: str = Field(description="Modelo elegido en la validación")
    entrenado_hasta: str = Field(description="Última hora usada para entrenar")
    metricas_test: dict[str, float | None] = Field(description="Métricas en el periodo de test reservado")
    fuente_datos: dict[str, str] = Field(description="Origen de cada dato del entrenamiento")


class SolicitudPronostico(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"fecha": "2026-10-23"}]})
    fecha: dt.date = Field(description="Día que se quiere pronosticar (AAAA-MM-DD)")


class FilaPronostico(BaseModel):
    timestamp: dt.datetime = Field(description="Hora pronosticada (hora de Medellín)")
    subestacion: str = Field(description="Código de la subestación")
    pronostico_kwh: float = Field(description="Energía pronosticada en la hora (kWh)")
    banda_alerta_kwh: float = Field(description="Consumo a partir del cual la hora se considera anómala (kWh)")
    costo_cop: float = Field(description="Costo estimado de la hora (COP)")


class Resumen(BaseModel):
    fecha: dt.date
    filas: int = Field(description="Número de filas (9 subestaciones × 24 horas)")
    total_kwh: float = Field(description="Energía pronosticada del día (kWh)")
    costo_total_cop: float = Field(description="Costo estimado del día (COP)")
    horas_con_dato_real: int = Field(description="Combinaciones subestación-hora del día con consumo real válido")
    horas_sobre_banda: int | None = Field(
        description="Combinaciones subestación-hora cuyo consumo real superó la banda; nulo si el día aún no tiene datos")
    igual_al_emitido_en_gold: bool | None = Field(
        description="Si el Job ya emitió ese día: ¿el cálculo de la API coincide con la tabla Gold? Nulo si no hay emisión")
    fuente_datos: str = Field(description="De dónde salió el consumo usado")


class RespuestaPronostico(BaseModel):
    resumen: Resumen
    pronostico: list[FilaPronostico]


class FilaEmitida(BaseModel):
    timestamp: dt.datetime = Field(description="Hora pronosticada (hora de Medellín)")
    fecha_hora_bogota: str = Field(description="La misma hora, en texto")
    subestacion: str
    pronostico_kwh: float
    banda_alerta_kwh: float
    costo_cop: float


class EntradaPrediccion(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"features": {"hora": 14, "laboral": 1, "nivel_7d": 120.5}}]})
    features: dict = Field(description="Variables del modelo para UNA fila (ver GET /modelo/variables)")


class ErrorDetalle(BaseModel):
    detail: str | list = Field(description="Motivo del error")


# ---- Estado ------------------------------------------------------------------------------------------
@app.get("/health", tags=["Estado"], summary="¿La API está arriba? ¿De dónde lee?", response_model=Salud)
def health(request: Request):
    """Confirma que la API está lista, qué versión del modelo tiene cargada y de dónde salió cada dato."""
    ctx = request.app.state.ctx
    return {"estado": "ok", "version_modelo": ctx.artefacto["version"], "modelo": ctx.artefacto["modelo"],
            "ultimo_dato": ctx.ultimo_dato,
            "pronosticos_emitidos_cargados": 0 if ctx.emitido is None else len(ctx.emitido),
            "fuente_datos": ctx.fuente}


@app.get("/salud", include_in_schema=False, response_model=Salud)
def salud(request: Request):
    return health(request)


@app.get("/modelo", tags=["Estado"], summary="Ficha del modelo", response_model=FichaModelo)
def modelo(request: Request):
    """Versión, tipo de modelo, fecha de entrenamiento, métricas del test y origen de los datos."""
    art = request.app.state.ctx.artefacto
    metricas = {k: (float(v) if math.isfinite(v) else None) for k, v in art["metricas_test"].items()
                if isinstance(v, (int, float))}           # se omite la marca de texto «elegido»
    return {"version": art["version"], "tipo_modelo": art["modelo"], "entrenado_hasta": art["entrenado_hasta"],
            "metricas_test": metricas, "fuente_datos": art["fuente_datos"]}


@app.post("/recargar", tags=["Estado"], summary="Volver a leer los datos")
def recargar(request: Request):
    """Vuelve a leer modelo y datos (Databricks o respaldo) sin reiniciar la API; útil después de correr el Job."""
    try:
        request.app.state.ctx = nucleo.cargar_contexto()
    except (nucleo.ArchivoFaltante, fuentes.SinDatabricks) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    ctx = request.app.state.ctx
    return {"fuente_datos": ctx.fuente, "ultimo_dato": ctx.ultimo_dato,
            "filas_emitidas": 0 if ctx.emitido is None else len(ctx.emitido)}


# ---- Calcular el pronóstico de un día -----------------------------------------------------------------
@app.post("/pronostico", tags=["Pronóstico"], summary="Calcular el pronóstico de un día",
          response_model=RespuestaPronostico,
          responses={422: {"model": ErrorDetalle, "description": "Fecha mal escrita, sin historia suficiente o "
                                                                 "posterior al último dato + 1 día"}})
def pronostico(solicitud: SolicitudPronostico, request: Request):
    """Calcula con el modelo las 216 filas (9 subestaciones × 24 horas) del día pedido y un resumen.

    Usa el consumo original hasta las 23:00 del día anterior (de Silver en Databricks o del respaldo local).
    Si el Job ya emitió ese día, compara el resultado con la tabla Gold. Para fechas ya observadas cuenta además
    las horas en que el consumo real superó la banda de alerta. Esas fechas hacen parte del entrenamiento del
    modelo final: sirven para explorar, no para medir su error (el error honesto es el del test, en `/modelo`).
    """
    ctx = request.app.state.ctx
    try:
        filas = nucleo.pronosticar_dia(solicitud.fecha, ctx)
    except nucleo.FechaNoValida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    filas = filas.assign(subestacion=filas.subestacion.astype(str))
    real = ctx.limpio.stack(future_stack=True).rename("real")
    real = filas[["timestamp", "subestacion"]].join(real, on=["timestamp", "subestacion"]).real
    con_dato = int(real.notna().sum())
    return {
        "resumen": {"fecha": solicitud.fecha, "filas": len(filas), "total_kwh": round(float(filas.pronostico_kwh.sum()), 1),
                    "costo_total_cop": round(float(filas.costo_cop.sum())), "horas_con_dato_real": con_dato,
                    "horas_sobre_banda": int((real > filas.banda_alerta_kwh).sum()) if con_dato else None,
                    "igual_al_emitido_en_gold": coincide_con_emitido(filas, ctx.emitido),
                    "fuente_datos": ctx.fuente["consumo"]},
        "pronostico": filas[["timestamp", "subestacion", "pronostico_kwh", "banda_alerta_kwh",
                             "costo_cop"]].to_dict("records"),
    }


def coincide_con_emitido(filas, emitido, tolerancia_kwh=1e-6):
    if emitido is None:
        return None
    e = emitido[emitido.timestamp.dt.normalize().isin(filas.timestamp.dt.normalize().unique())]
    if e.empty:
        return None
    j = filas.merge(e, on=["timestamp", "subestacion"], suffixes=("", "_gold"))
    return bool(len(j) == len(filas) and (j.pronostico_kwh - j.pronostico_kwh_gold).abs().max() < tolerancia_kwh)


# ---- Consultar el pronóstico emitido (Gold) -------------------------------------------------------------
def _emitido_de(ctx, subestacion):
    if ctx.emitido is None or ctx.emitido.empty:
        raise HTTPException(status_code=503, detail="No hay pronóstico emitido cargado (ni de Gold ni del respaldo).")
    sub = fuentes.codigo_subestacion(pd.Series([subestacion])).iloc[0]
    d = ctx.emitido[ctx.emitido.subestacion == sub].copy()
    if d.empty:
        raise HTTPException(status_code=404, detail=f"Subestación '{sub}' no encontrada. Disponibles: "
                                                    f"{', '.join(sorted(ctx.emitido.subestacion.unique()))}")
    d["fecha_hora_bogota"] = d.timestamp.dt.strftime("%Y-%m-%d %H:%M")
    d["pronostico_kwh"] = d.pronostico_kwh.round(1)
    d["banda_alerta_kwh"] = d.banda_alerta_kwh.round(1)
    d["costo_cop"] = d.costo_cop.round(0)
    return d[["timestamp", "fecha_hora_bogota", "subestacion", "pronostico_kwh", "banda_alerta_kwh", "costo_cop"]]


@app.get("/pronostico", tags=["Resultados en Gold"], summary="Pronóstico emitido para una subestación",
         response_model=list[FilaEmitida], responses={404: {"model": ErrorDetalle}, 503: {"model": ErrorDetalle}})
def pronostico_emitido(request: Request,
                       subestacion: str = Query(description="Código de la subestación (ver GET /health o la respuesta "
                                                            "de POST /pronostico)")):
    """Devuelve las 24 horas que el Job guardó en `gold_pronostico_emitido` para esa subestación (redondeado)."""
    return _emitido_de(request.app.state.ctx, subestacion).to_dict("records")


@app.get("/pronostico/tabla", tags=["Resultados en Gold"], summary="Lo mismo, como tabla HTML",
         response_class=HTMLResponse)
def pronostico_tabla(request: Request, subestacion: str = Query(description="Código de la subestación")):
    ctx = request.app.state.ctx
    d = _emitido_de(ctx, subestacion).drop(columns=["timestamp"])
    return (f"<h2>Pronóstico {html.escape(subestacion.upper())} (fuente: {html.escape(ctx.fuente['emitido'])})</h2>"
            + d.to_html(index=False))


# ---- Usar el modelo con variables ya calculadas -------------------------------------------------------
@app.get("/modelo/variables", tags=["Modelo"], summary="Variables que necesita el modelo")
def modelo_variables(request: Request):
    art = request.app.state.ctx.artefacto
    vs = {v for c in art["componentes"] for v in c["variables"]}
    return {"version": art.get("version"),
            "variables_requeridas": sorted(vs | {"subestacion", "hora", "laboral", "nivel_7d"})}


@app.post("/predecir", tags=["Modelo"], summary="Predecir una fila con variables ya calculadas",
          responses={422: {"model": ErrorDetalle}})
def predecir(payload: EntradaPrediccion, request: Request):
    """Aplica el modelo a UNA fila de variables (las de `/modelo/variables`). Para un día completo, usar
    `POST /pronostico`, que calcula las variables a partir de los datos."""
    ctx = request.app.state.ctx
    try:
        pred = float(nucleo.predecir_con_artefacto(ctx.artefacto, pd.DataFrame([payload.features]))[0])
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"Falta la variable {e}. Mira /modelo/variables") from e
    mes = (ctx.ultimo_dato + pd.Timedelta(days=1)).strftime("%Y-%m")
    return {"pronostico_kwh": round(pred, 3), "costo_estimado_cop": round(pred * ctx.tarifas[mes], 2),
            "version": ctx.artefacto.get("version")}
