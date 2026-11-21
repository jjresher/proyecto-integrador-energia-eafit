# API del modelo (SI7006, criterio 4: despliegue de modelos y consumo, 20 %)

*Estado: pendiente.*

La rúbrica pide el modelo **publicado mediante una API, con una prueba funcional**. Plan:

- **Tecnología:** FastAPI.
- **Modelo:** carga `modelo_energia_v4.joblib` (versión completa del equipo; el `.joblib` no se publica en este repositorio) y pronostica con la función `predecir_con_artefacto` del notebook
  (sección 13.1b), la misma que se comprobó contra el modelo en memoria.
- **Endpoints previstos:**
  - `GET /salud`: la API está arriba y qué versión del modelo tiene cargada.
  - `GET /modelo`: ficha del modelo (versión, fecha de entrenamiento, métricas del test, origen de los datos).
  - `POST /pronostico`: recibe una fecha y devuelve las 24 horas × 9 subestaciones con pronóstico (kWh), banda de alerta y costo
    estimado.
- **Prueba funcional:** un archivo de pruebas que levanta la API y compara su pronóstico del 23-oct-2026 con el que guardó el
  notebook (`pronostico_2026-10-23.csv`, en la versión completa del equipo).
