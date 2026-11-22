"""Prueba funcional de la API. Necesita el modelo y los datos del equipo (no se publican)."""
import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import nucleo
from api.main import app

FECHA = "2026-10-23"
REFERENCIA = Path(os.environ.get("RUTA_REFERENCIA") or nucleo.RAIZ / f"modelo/resultados/pronostico_{FECHA}.csv")


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def respuesta(cliente):
    r = cliente.post("/pronostico", json={"fecha": FECHA})
    assert r.status_code == 200, r.text
    return r.json()


def test_salud(cliente):
    for ruta in ("/health", "/salud"):
        r = cliente.get(ruta)
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["estado"] == "ok" and cuerpo["version_modelo"]
        assert {"consumo", "clima", "emitido"} <= set(cuerpo["fuente_datos"])
        assert cuerpo["pronosticos_emitidos_cargados"] == 216


def test_modelo(cliente):
    r = cliente.get("/modelo")
    assert r.status_code == 200
    ficha = r.json()
    assert {"version", "tipo_modelo", "entrenado_hasta", "metricas_test", "fuente_datos"} <= set(ficha)
    assert "WAPE_%" in ficha["metricas_test"]


def test_pronostico_216_combinaciones(respuesta):
    df = pd.DataFrame(respuesta["pronostico"])
    assert len(df) == 216
    assert len(df[["subestacion", "timestamp"]].drop_duplicates()) == 216
    assert df.subestacion.nunique() == 9
    horas = pd.to_datetime(df.timestamp)
    assert set(horas.dt.hour) == set(range(24)) and set(horas.dt.strftime("%Y-%m-%d")) == {FECHA}


def test_pronostico_igual_al_del_notebook(respuesta):
    api = pd.DataFrame(respuesta["pronostico"])
    api["timestamp"] = pd.to_datetime(api.timestamp)
    ref = pd.read_csv(REFERENCIA, parse_dates=["timestamp"])
    j = ref.merge(api, on=["timestamp", "subestacion"], suffixes=("_ref", "_api"), validate="one_to_one")
    assert len(j) == len(ref) == 216
    for columna, tolerancia in (("pronostico_kwh", 1e-6), ("banda_alerta_kwh", 1e-6), ("costo_cop", 1e-3)):
        diferencia = (j[columna + "_ref"] - j[columna + "_api"]).abs().max()
        assert diferencia < tolerancia, f"{columna}: diferencia máxima {diferencia}"


def test_resumen(respuesta):
    df, resumen = pd.DataFrame(respuesta["pronostico"]), respuesta["resumen"]
    assert resumen["total_kwh"] == pytest.approx(df.pronostico_kwh.sum(), abs=0.05)       # redondeado a 0,1 kWh
    assert resumen["costo_total_cop"] == pytest.approx(df.costo_cop.sum(), abs=0.5)       # redondeado al peso
    assert resumen["horas_sobre_banda"] is None            # ese día todavía no tiene consumo real
    assert resumen["igual_al_emitido_en_gold"] is True     # el cálculo de la API = lo que el Job guardó en Gold


def test_dia_observado_cuenta_horas_sobre_banda(cliente):
    r = cliente.post("/pronostico", json={"fecha": "2026-09-15"})
    assert r.status_code == 200, r.text
    resumen = r.json()["resumen"]
    assert 0 <= resumen["horas_sobre_banda"] <= resumen["horas_con_dato_real"] <= 216
    assert resumen["horas_con_dato_real"] > 0


@pytest.mark.parametrize("fecha, texto", [
    ("2026-10-24", "posterior"), ("2027-01-01", "posterior"),
    ("2020-01-01", "historia"), ("2025-04-16", "historia")])
def test_fecha_fuera_de_rango(cliente, fecha, texto):
    r = cliente.post("/pronostico", json={"fecha": fecha})
    assert r.status_code == 422
    assert texto in r.json()["detail"]


def test_pronostico_emitido_por_subestacion(cliente):
    sub = cliente.app.state.ctx.subestaciones[0]
    r = cliente.get("/pronostico", params={"subestacion": sub.lower()})
    assert r.status_code == 200, r.text
    filas = r.json()
    assert len(filas) == 24 and {f["subestacion"] for f in filas} == {sub}
    assert cliente.get("/pronostico", params={"subestacion": "NO_EXISTE"}).status_code == 404
    tabla = cliente.get("/pronostico/tabla", params={"subestacion": sub})
    assert tabla.status_code == 200 and "<table" in tabla.text


def test_emitido_coincide_con_el_calculo(cliente, respuesta):
    calculado = pd.DataFrame(respuesta["pronostico"])
    sub = calculado.subestacion.iloc[0]
    emitido = pd.DataFrame(cliente.get("/pronostico", params={"subestacion": sub}).json())
    c = calculado[calculado.subestacion == sub].pronostico_kwh.round(1).to_numpy()
    assert (emitido.pronostico_kwh.to_numpy() == c).all()


def test_predecir_una_fila_da_lo_mismo_que_el_dia(cliente, respuesta):
    ctx = cliente.app.state.ctx
    fecha = pd.Timestamp(FECHA)
    T = nucleo.tabla_desde_crudo(ctx.crudo.loc[:fecha - pd.Timedelta(hours=1)], ctx,
                                 hasta=fecha + pd.Timedelta(hours=23))
    variables = cliente.get("/modelo/variables").json()["variables_requeridas"]
    T = T[T.timestamp.dt.normalize() == fecha].dropna(subset=variables)
    fila = T.iloc[10]
    features = {v: (fila[v].item() if hasattr(fila[v], "item") else str(fila[v])) for v in variables}
    r = cliente.post("/predecir", json={"features": features})
    assert r.status_code == 200, r.text
    dia = pd.DataFrame(respuesta["pronostico"])
    dia["timestamp"] = pd.to_datetime(dia.timestamp)
    esperado = dia[(dia.subestacion == str(fila.subestacion)) & (dia.timestamp == fila.timestamp)].pronostico_kwh.item()
    assert r.json()["pronostico_kwh"] == pytest.approx(esperado, abs=5e-4)
    sin_variable = cliente.post("/predecir", json={"features": {"hora": 3}})
    assert sin_variable.status_code == 422 and "/modelo/variables" in sin_variable.json()["detail"]


def test_recargar(cliente):
    r = cliente.post("/recargar")
    assert r.status_code == 200 and r.json()["filas_emitidas"] == 216


def test_documentacion_sin_internet(cliente):
    assert cliente.get("/", follow_redirects=False).headers["location"] == "/docs"
    html = cliente.get("/docs").text
    assert "cdn.jsdelivr.net" not in html, "la página /docs no debe depender de internet"
    assert cliente.get("/archivos-docs/swagger-ui-bundle.js").status_code == 200


def test_fecha_mal_escrita(cliente):
    assert cliente.post("/pronostico", json={"fecha": "23/10/2026"}).status_code == 422


@pytest.mark.parametrize("variable", list(nucleo.RUTAS))
def test_no_arranca_si_falta_un_archivo(monkeypatch, tmp_path, variable):
    monkeypatch.setenv("FUENTE_DATOS", "local")            # con Databricks, los archivos de datos no hacen falta
    monkeypatch.setenv(variable, str(tmp_path / "no_existe.xyz"))
    with pytest.raises(nucleo.ArchivoFaltante, match="no_existe.xyz.*solo lo tiene el equipo"):
        with TestClient(app):
            pass
