# API del modelo (SI7006, despliegue de modelos y consumo)

Publica con FastAPI el modelo ya entrenado y guardado (versión v4). Recibe una **fecha** y devuelve el pronóstico
**hora a hora de ese día para las 9 subestaciones**: energía (kWh), banda de alerta (kWh) y costo estimado (COP).

En el proyecto es el paso final: convierte el modelo del notebook en un servicio que pueden consultar el tablero o el
área de mantenimiento sin abrir el notebook. Cada pronóstico usa solo datos hasta las 23:00 del día anterior.

| Archivo | Contenido |
|---|---|
| `nucleo.py` | Lectura, limpieza, variables y predicción: la misma lógica del notebook |
| `main.py` | Endpoints y validaciones |
| `tests/test_api.py` | Prueba funcional: compara la API con el pronóstico que guardó el notebook |

## 🔒 El modelo y los datos no se publican

Esta carpeta contiene solo código. Para correr la API se necesitan archivos que **solo tiene el equipo**. Si falta alguno,
la API no arranca y dice cuál es. Las rutas se pueden cambiar con variables de entorno (por defecto, relativas a la raíz
del repositorio):

| Variable | Valor por defecto | Contenido |
|---|---|---|
| `RUTA_MODELO` | `modelo/modelos/modelo_energia_v4.joblib` | Modelo entrenado |
| `RUTA_CONSUMO` | `datos/Subestaciones_15_abril_2025_-_22_oct_2026.xlsx` | Consumo horario, una hoja por subestación |
| `RUTA_CLIMA` | `datos/clima_nasa_power_eafit.csv` | Clima horario (NASA POWER) |
| `RUTA_PRIVADO` | `datos/privado.json` | Configuración confidencial: `capacidad_kva`, `tarifa_cop_kwh` y `reglas_limpieza` |

Los tests también leen el pronóstico de referencia `modelo/resultados/pronostico_2026-10-23.csv` (`RUTA_REFERENCIA`).

⚠️ La API no pide usuario y sus respuestas contienen datos reales: usarla solo en el computador local
(`127.0.0.1`, el valor por defecto de uvicorn) y no publicar sus respuestas.

## Instalar

Desde la raíz del repositorio:

```bash
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r api/requirements.txt
```

## Correr

```bash
uvicorn api.main:app --reload
```

Documentación interactiva (Swagger): <http://127.0.0.1:8000/docs>

| Endpoint | Qué devuelve |
|---|---|
| `GET /salud` | Estado y versión del modelo cargado |
| `GET /modelo` | Versión, tipo de modelo, hasta cuándo se entrenó, métricas del test y origen de los datos |
| `POST /pronostico` | 216 filas (9 × 24) y un resumen: total kWh, costo total y horas sobre la banda (si ya hay consumo real) |

Responde **422**, con el motivo, si la fecha está mal escrita, es posterior al último dato + 1 día, o alguna subestación no
tiene al menos 72 horas válidas en los 7 días anteriores.

Las fechas ya observadas hacen parte del entrenamiento del modelo final: sirven para explorar, no para medir el error.
El error honesto es el del test, que está en `/modelo`.

## Tests

```bash
pytest api/tests -v
```

## Guion de demo (1 minuto)

1. **(10 s)** Abrir <http://127.0.0.1:8000/docs>: «El modelo está publicado como una API con tres servicios».
2. **(10 s)** `GET /salud` → *Try it out* → *Execute*: «Está arriba y tiene cargada la versión v4».
   ```bash
   curl http://127.0.0.1:8000/salud
   ```
3. **(15 s)** `GET /modelo`: «Esta es la ficha: tipo de modelo, hasta cuándo se entrenó y sus métricas en el test».
   ```bash
   curl http://127.0.0.1:8000/modelo
   ```
4. **(25 s)** `POST /pronostico` con `2026-10-23`: «Devuelve las 24 horas de las 9 subestaciones, la banda de alerta, el costo
   y el total del día. Es el mismo pronóstico del notebook: lo comprueba la prueba funcional (diferencia menor que 1e-6 kWh)».
   ```bash
   curl -X POST http://127.0.0.1:8000/pronostico \
        -H "Content-Type: application/json" -d '{"fecha": "2026-10-23"}'
   ```
   Si preguntan por las validaciones, probar `2027-01-01`: responde 422 y explica por qué.
