"""API del pronóstico horario de energía. Correr desde la raíz del repositorio: uvicorn api.main:app --reload"""
import datetime as dt
import logging
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api import nucleo

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Modelo y datos se cargan una sola vez; si falta un archivo, la API no arranca
    try:
        app.state.ctx = nucleo.cargar_contexto()
    except nucleo.ArchivoFaltante as e:
        log.error("%s", e)
        raise
    yield


app = FastAPI(
    title="API de pronóstico de energía",
    version="1.0.0",
    lifespan=ciclo_de_vida,
    description="Pronóstico **horario** de la energía comprada a la red por 9 subestaciones de un campus universitario, "
                "emitido a las 00:00 de cada día solo con datos hasta las 23:00 del día anterior.\n\n"
                "Proyecto integrador · SI7006, despliegue de modelos y consumo.",
    openapi_tags=[{"name": "Estado", "description": "Disponibilidad de la API y ficha del modelo."},
                  {"name": "Pronóstico", "description": "Pronóstico de un día para todas las subestaciones."}],
)


class Salud(BaseModel):
    estado: str = Field(description="«ok» si la API está lista", examples=["ok"])
    version_modelo: str = Field(description="Versión del modelo cargado", examples=["v4"])


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
    timestamp: dt.datetime = Field(description="Hora pronosticada (hora local)")
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


class RespuestaPronostico(BaseModel):
    resumen: Resumen
    pronostico: list[FilaPronostico]


class ErrorDetalle(BaseModel):
    detail: str | list = Field(description="Motivo por el que no se puede pronosticar la fecha")


@app.get("/salud", tags=["Estado"], summary="¿La API está arriba?", response_model=Salud)
def salud(request: Request):
    """Confirma que la API está lista y dice qué versión del modelo tiene cargada."""
    return {"estado": "ok", "version_modelo": request.app.state.ctx.artefacto["version"]}


@app.get("/modelo", tags=["Estado"], summary="Ficha del modelo", response_model=FichaModelo)
def modelo(request: Request):
    """Versión, tipo de modelo, fecha de entrenamiento, métricas del test y origen de los datos."""
    art = request.app.state.ctx.artefacto
    metricas = {k: (float(v) if math.isfinite(v) else None) for k, v in art["metricas_test"].items()
                if isinstance(v, (int, float))}           # se omite la marca de texto «elegido»
    return {"version": art["version"], "tipo_modelo": art["modelo"], "entrenado_hasta": art["entrenado_hasta"],
            "metricas_test": metricas, "fuente_datos": art["fuente_datos"]}


@app.post("/pronostico", tags=["Pronóstico"], summary="Pronosticar un día", response_model=RespuestaPronostico,
          responses={422: {"model": ErrorDetalle, "description": "Fecha mal escrita, sin historia suficiente o "
                                                                 "posterior al último dato + 1 día"}})
def pronostico(solicitud: SolicitudPronostico, request: Request):
    """Devuelve 216 filas (9 subestaciones × 24 horas) y un resumen del día.

    La fecha debe tener historia previa suficiente en las 9 subestaciones y no ser posterior al último dato + 1 día.
    Para fechas ya observadas también se cuentan las horas en que el consumo real superó la banda de alerta.
    Esas fechas hacen parte del entrenamiento del modelo final: sirven para explorar, no para medir su error
    (el error honesto es el del test, en `/modelo`).
    """
    ctx = request.app.state.ctx
    try:
        filas = nucleo.pronosticar_dia(solicitud.fecha, ctx)
    except nucleo.FechaNoValida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    real = ctx.limpio.stack(future_stack=True).rename("real")
    real = filas[["timestamp", "subestacion"]].astype({"subestacion": str}).join(real, on=["timestamp", "subestacion"]).real
    con_dato = int(real.notna().sum())
    return {
        "resumen": {"fecha": solicitud.fecha, "filas": len(filas), "total_kwh": filas.pronostico_kwh.sum(),
                    "costo_total_cop": filas.costo_cop.sum(), "horas_con_dato_real": con_dato,
                    "horas_sobre_banda": int((real > filas.banda_alerta_kwh).sum()) if con_dato else None},
        "pronostico": filas[["timestamp", "subestacion", "pronostico_kwh", "banda_alerta_kwh",
                             "costo_cop"]].to_dict("records"),
    }
