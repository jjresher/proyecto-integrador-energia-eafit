"""Simula el SQL Warehouse de Databricks con los archivos locales, para probar sin conexión la lectura de Silver y
Gold: nombres de columna, zonas horarias y el respaldo cuando Databricks falla. La prueba REAL contra Databricks es
la de test_api.py corrida con un archivo .env con credenciales."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import fuentes, nucleo
from api.main import app

CREDENCIALES = {"DATABRICKS_HOST": "simulado.cloud.databricks.com", "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/x",
                "DATABRICKS_TOKEN": "simulado"}


def tablas_simuladas(guardado):
    """Tablas como las entregaría el conector: TIMESTAMP con zona UTC.
    guardado="reloj": la hora de reloj se guardó tal cual (00:00 local -> 00:00 UTC);
    guardado="convertido": se convirtió de hora de Medellín a UTC (00:00 local -> 05:00 UTC), como en Gold."""
    def con_zona(ts):
        ts = pd.to_datetime(ts)
        return ts.dt.tz_localize("UTC") if guardado == "reloj" else ts.dt.tz_localize("America/Bogota").dt.tz_convert("UTC")

    largo = nucleo.cargar_subestaciones(nucleo.ruta_de("RUTA_CONSUMO"))
    # Mismas columnas que escribe 02_silver: «kwh» es el valor original y «kwh_limpio» el limpio (no se debe usar)
    consumo = pd.DataFrame({"subestacion": largo.subestacion, "ts": con_zona(largo.timestamp),
                            "fecha": largo.timestamp.dt.date, "hora": largo.timestamp.dt.hour, "kwh": largo.kwh,
                            "kwh_limpio": -1.0, "causa_invalido": None})
    clima_csv = pd.read_csv(nucleo.ruta_de("RUTA_CLIMA"))
    clima = pd.DataFrame({"ts": con_zona(clima_csv.timestamp), "t2m": clima_csv.T2M,
                          "prectotcorr": clima_csv.PRECTOTCORR, "allsky_sfc_sw_dwn": clima_csv.ALLSKY_SFC_SW_DWN})
    emitido_csv = pd.read_csv(nucleo.ruta_de("RUTA_PRONOSTICO"))
    emitido = emitido_csv.assign(timestamp=pd.to_datetime(emitido_csv.timestamp).dt.tz_localize("America/Bogota")
                                 .dt.tz_convert("UTC"))
    return {fuentes.TABLAS["consumo"]: consumo, fuentes.TABLAS["clima"]: clima, fuentes.TABLAS["emitido"]: emitido}


@pytest.mark.parametrize("guardado", ["reloj", "convertido"])
def test_lee_silver_y_gold_desde_databricks(monkeypatch, guardado):
    tablas = tablas_simuladas(guardado)
    for k, v in CREDENCIALES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FUENTE_DATOS", "databricks")
    monkeypatch.setattr(fuentes, "consultar", lambda tabla: tablas[tabla].copy())
    with TestClient(app) as c:
        salud = c.get("/health").json()
        assert all(salud["fuente_datos"][k].startswith("Databricks") for k in ("consumo", "clima", "emitido"))
        assert salud["ultimo_dato"].startswith("2026-10-22T23:00")
        r = c.post("/pronostico", json={"fecha": "2026-10-23"})
        assert r.status_code == 200, r.text
        assert r.json()["resumen"]["igual_al_emitido_en_gold"] is True
        ref = pd.read_csv(nucleo.ruta_de("RUTA_PRONOSTICO"), parse_dates=["timestamp"])
        api = pd.DataFrame(r.json()["pronostico"]).assign(timestamp=lambda d: pd.to_datetime(d.timestamp))
        j = ref.merge(api, on=["timestamp", "subestacion"], suffixes=("_ref", "_api"))
        assert len(j) == 216 and (j.pronostico_kwh_ref - j.pronostico_kwh_api).abs().max() < 1e-6
        emitido = c.get("/pronostico", params={"subestacion": j.subestacion.iloc[0]}).json()
        assert emitido[0]["fecha_hora_bogota"] == "2026-10-23 00:00"


def test_si_databricks_falla_usa_el_respaldo(monkeypatch):
    for k, v in CREDENCIALES.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FUENTE_DATOS", "auto")

    def sin_conexion(tabla):
        raise ConnectionError("warehouse apagado")
    monkeypatch.setattr(fuentes, "consultar", sin_conexion)
    with TestClient(app) as c:
        fuente = c.get("/health").json()["fuente_datos"]
        assert fuente["consumo"].startswith("archivo local") and "no respondió" in fuente["avisos"]
        assert c.post("/pronostico", json={"fecha": "2026-10-23"}).json()["resumen"]["igual_al_emitido_en_gold"] is True


def test_databricks_obligatorio_sin_credenciales_no_arranca(monkeypatch):
    for k in (*CREDENCIALES, "DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_WAREHOUSE_ID"):
        monkeypatch.setenv(k, "")                  # vacías: un .env local no las puede llenar
    monkeypatch.setenv("FUENTE_DATOS", "databricks")
    with pytest.raises(fuentes.SinDatabricks, match="faltan las credenciales"):
        with TestClient(app):
            pass


def test_hora_local():
    reloj = pd.Series(pd.to_datetime(["2026-10-22 22:00", "2026-10-22 23:00"]).tz_localize("UTC"))
    convertido = pd.Series(pd.to_datetime(["2026-10-23 03:00", "2026-10-23 04:00"]).tz_localize("UTC"))
    esperado = list(pd.to_datetime(["2026-10-22 22:00", "2026-10-22 23:00"]))
    assert list(fuentes.hora_local(reloj)) == esperado
    assert list(fuentes.hora_local(convertido)) == esperado
