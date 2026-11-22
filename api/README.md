# API del modelo (SI7006, despliegue de modelos y consumo)

Publica con FastAPI el modelo ya entrenado y versionado (v4) y lo conecta con Databricks. Une las dos versiones que hizo
el equipo:

- **Calcular** (`POST /pronostico`): recibe una **fecha** y el modelo pronostica ese día, hora a hora, para las 9
  subestaciones. Lee el consumo **original** de la tabla Silver en Databricks y aplica la misma limpieza causal y las
  mismas variables del notebook.
- **Consultar** (`GET /pronostico?subestacion=`): devuelve el pronóstico que el **Job ya guardó en Gold**
  (`gold_pronostico_emitido`), leído desde el SQL Warehouse.

Las dos cosas se comprueban entre sí: cuando el Job ya emitió el día pedido, `POST /pronostico` dice si su cálculo es
idéntico al de la tabla Gold (`igual_al_emitido_en_gold`).

| Archivo | Contenido |
|---|---|
| `main.py` | Endpoints y validaciones |
| `nucleo.py` | Limpieza, variables y predicción: la misma lógica del notebook |
| `fuentes.py` | Lectura de Databricks (Silver y Gold) y respaldo en archivos locales |
| `tests/` | Pruebas funcionales (25), incluida una simulación del SQL Warehouse |
| `docs_estaticos/` | Archivos de Swagger UI, para que `/docs` funcione sin internet |

## De dónde lee

| `FUENTE_DATOS` | Comportamiento |
|---|---|
| `auto` (por defecto) | Databricks si hay credenciales y responde; si no, los archivos locales |
| `databricks` | Obliga a usar Databricks; si no puede, la API no arranca y dice por qué |
| `local` | Solo archivos locales (demo sin internet) |

`GET /health` muestra de dónde salió cada dato. Si Databricks no responde en modo `auto`, la API sigue funcionando con
los archivos y lo avisa ahí mismo.

Credenciales: copiar `api/.env.ejemplo` como `api/.env` y llenarlo. **El `.env` nunca se sube** (está en `.gitignore`).
Dentro de una Databricks App no hace falta token: basta `DATABRICKS_WAREHOUSE_ID`.

Tablas que usa (nombres y columnas aceptadas en `fuentes.py`):

| Dato | Tabla |
|---|---|
| Consumo horario original | `workspace.eafit_energia.silver_consumo` (`kwh_original`) |
| Clima horario | `workspace.eafit_energia.silver_clima` |
| Pronóstico emitido por el Job | `workspace.eafit_energia.gold_pronostico_emitido` |

## 🔒 El modelo y los datos no se publican

Esta carpeta contiene solo código. Para correr la API se necesitan archivos que **solo tiene el equipo**. Si falta alguno,
la API no arranca y dice cuál es:

| Variable | Valor por defecto | Contenido |
|---|---|---|
| `RUTA_MODELO` | `modelo/modelos/modelo_energia_v4.joblib` | Modelo entrenado |
| `RUTA_PRIVADO` | `datos/privado.json` | Configuración confidencial: `capacidad_kva`, `tarifa_cop_kwh` y `reglas_limpieza` |
| `RUTA_CONSUMO` | `datos/Subestaciones_15_abril_2025_-_22_oct_2026.xlsx` | Respaldo del consumo (si no se lee de Databricks) |
| `RUTA_CLIMA` | `datos/clima_nasa_power_eafit.csv` | Respaldo del clima |
| `RUTA_PRONOSTICO` | `modelo/resultados/pronostico_2026-10-23.csv` | Respaldo del pronóstico emitido (opcional) |

⚠️ La API no pide usuario y sus respuestas contienen datos reales: usarla solo en el computador local
(`127.0.0.1`, el valor por defecto de uvicorn) y no publicar sus respuestas.

## Instalar

Desde la raíz del repositorio:

```bash
python -m venv .venv
source .venv/bin/activate                        # en Windows: .venv\Scripts\activate
pip install -r api/requirements-databricks.txt   # o api/requirements.txt para usarla solo con archivos locales
```

## Correr

```bash
uvicorn api.main:app --reload
```

Abrir <http://127.0.0.1:8000> (lleva a la documentación interactiva, que funciona sin internet).

| Endpoint | Qué hace |
|---|---|
| `GET /health` | Estado, versión del modelo y de dónde salió cada dato |
| `GET /modelo` | Ficha: tipo de modelo, hasta cuándo se entrenó, métricas del test |
| `POST /recargar` | Vuelve a leer los datos sin reiniciar (por ejemplo, después de correr el Job) |
| `POST /pronostico` | **Calcula** el día pedido: 216 filas (9 × 24) y un resumen |
| `GET /pronostico?subestacion=` | **Consulta** las 24 horas emitidas por el Job en Gold para una subestación |
| `GET /pronostico/tabla?subestacion=` | Lo mismo, como tabla HTML |
| `GET /modelo/variables` | Variables que necesita el modelo |
| `POST /predecir` | Aplica el modelo a una fila de variables ya calculadas |

`POST /pronostico` responde **422**, con el motivo, si la fecha está mal escrita, es posterior al último dato + 1 día, o
alguna subestación no tiene al menos 72 horas válidas en los 7 días anteriores. Las fechas ya observadas hacen parte del
entrenamiento del modelo final: sirven para explorar, no para medir el error (el error honesto es el del test, en `/modelo`).

## Tests

```bash
pytest api/tests -v
```

- Sin credenciales, prueban la API con los archivos locales y **simulan** el SQL Warehouse (zonas horarias, nombres de
  columnas y respaldo cuando Databricks falla).
- **Con un `.env` válido, `test_api.py` prueba contra Databricks real:** el pronóstico calculado desde Silver debe ser
  idéntico (diferencia < 1e-6 kWh) al que guardó el notebook y al que el Job dejó en Gold. Para obligar a usar
  Databricks: `FUENTE_DATOS=databricks pytest api/tests -v` (en Windows PowerShell:
  `$env:FUENTE_DATOS="databricks"; pytest api/tests -v`).

## Guion de demo (1-2 minutos)

1. Abrir <http://127.0.0.1:8000>: «El modelo está publicado como una API».
2. `GET /health`: «Está arriba, tiene la versión v4 y lee de Databricks: Silver y Gold».
3. `POST /pronostico` con `2026-10-23`: «Le pido un día y el modelo lo calcula: las 24 horas de las 9 subestaciones, la
   banda de alerta y el costo. `igual_al_emitido_en_gold: true` dice que es idéntico a lo que guardó el Job».
4. `GET /pronostico?subestacion=…`: «Y esto es lo que otros sistemas consultan: el pronóstico ya guardado en Gold».
5. Si preguntan por validaciones: `POST /pronostico` con `2027-01-01` responde 422 y explica por qué.

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/pronostico -H "Content-Type: application/json" -d '{"fecha": "2026-10-23"}'
```
