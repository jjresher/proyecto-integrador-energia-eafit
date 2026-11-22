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
    r = cliente.get("/salud")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok" and r.json()["version_modelo"]


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
    assert resumen["total_kwh"] == pytest.approx(df.pronostico_kwh.sum())
    assert resumen["costo_total_cop"] == pytest.approx(df.costo_cop.sum())
    assert resumen["horas_sobre_banda"] is None            # ese día todavía no tiene consumo real


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


def test_fecha_mal_escrita(cliente):
    assert cliente.post("/pronostico", json={"fecha": "23/10/2026"}).status_code == 422


@pytest.mark.parametrize("variable", list(nucleo.RUTAS))
def test_no_arranca_si_falta_un_archivo(monkeypatch, tmp_path, variable):
    monkeypatch.setenv(variable, str(tmp_path / "no_existe.xyz"))
    with pytest.raises(nucleo.ArchivoFaltante, match="no_existe.xyz.*solo lo tiene el equipo"):
        with TestClient(app):
            pass
