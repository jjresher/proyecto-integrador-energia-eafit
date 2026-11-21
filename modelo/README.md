# Pronóstico horario de energía por subestación — Universidad EAFIT

Componente de Aprendizaje Automático (SI7009) del Proyecto Integrador. **Versión 4**: el modelo lee sus datos de las tablas del
pipeline **Medallion en Databricks** (Silver), en lugar de los Excel.

**Resultado:** el modelo elegido, **LightGBM ajustado**, tiene un WAPE de **9,32 %** en el test frente a **17,16 %** de la
regla "misma hora de la semana pasada" (−46 %), y le gana en las 9 subestaciones.

> 🌐 **Versión pública.** El notebook de esta carpeta está ejecutado de principio a fin con **datos transformados**: las
> subestaciones se llaman SUB1 a SUB9 y toda cifra de energía (kWh) y de costo (COP) está multiplicada por un factor
> confidencial. Las curvas, las proporciones y las
> métricas relativas (WAPE, MASE, % de mejora, sensibilidad y precisión de alertas) son **idénticas** a las de la versión con
> los valores reales. Las cifras absolutas en kWh y COP (MAE, RMSE, pesos) **no** son las reales.

## Contenido

| Archivo / carpeta | Qué es |
|---|---|
| `Pronostico_energia_EAFIT.ipynb` | Notebook principal, **ejecutado de principio a fin** (versión pública, con gráficos y tablas). Explicado paso a paso. |
| `Pronostico_energia_EAFIT.html` | El mismo notebook en HTML: descargarlo y abrirlo en el navegador para leerlo sin instalar nada. |
| `resultados/` | Resultados **agregados** de esa misma corrida: métricas del test, ranking de validación, hiperparámetros, alertas y tabla de correcciones. Los pronósticos hora a hora por subestación no se publican. |
| `modelos/ficha_modelo_v4.json` | Ficha del modelo: variables, hiperparámetros, métricas, umbrales relativos de alerta, versiones y origen de los datos. El archivo `.joblib` no se publica. |
| `.env.ejemplo` | Plantilla de credenciales para leer de Databricks desde un computador. Se copia como `.env`, que **no** se sube. |
| `requirements.txt` | Librerías necesarias, con las versiones usadas en la ejecución. |

## Dónde ver qué

- **Hallazgos principales con gráficos:** [`docs/hallazgos/`](../docs/hallazgos/).
- **Todo el análisis:** el notebook. Si GitHub no alcanza a mostrarlo (es grande), descargar el HTML.

## De dónde lee los datos

La celda 2.1 decide la fuente (`FUENTE = "auto"` por defecto):

| Dónde se corre | Cómo lee |
|---|---|
| **Dentro de Databricks** | Spark lee `workspace.eafit_energia.*` sin token |
| **Computador con credenciales** (`.env`) | Conector SQL contra un SQL warehouse |
| **Computador sin credenciales** | Archivos de `datos/` |

En los tres casos necesita además `datos/privado.json` (alias, capacidades y factor de escala), que tiene el equipo.

Tablas que usa (se cambian en `TABLAS` y `COLUMNAS` de la celda 2.1 si el pipeline usa otros nombres):

| Dato | Tabla | Columnas que busca |
|---|---|---|
| Energía horaria de las 9 subestaciones | `silver_consumo` | marca de tiempo (`ts` o `fecha` + `hora`), `subestacion`, **`kwh_original`** y la marca de calidad (`flag_calidad` o `causa_invalido`) |
| Clima horario NASA POWER | `silver_clima` | marca de tiempo, `T2M`, `PRECTOTCORR`, `ALLSKY_SFC_SW_DWN` (o `temperatura`, `precipitacion`, `radiacion`) |
| Medidor general (solo conciliación) | `silver_demanda_total` | marca de tiempo, `kwh_original` |
| Capacidad de cada transformador | `silver_dim_subestacion` | `subestacion`, `capacidad_kva` |

**Por qué el valor original y no el limpio de Silver o Gold:** la limpieza de Silver describe toda la historia (para el tablero
y la conciliación) y puede mirar el futuro; el modelo necesita una limpieza **causal** y la prueba de no-fuga reconstruye todo
desde el dato original recortado a las 23:00. La sección 4.1c compara las dos limpiezas.

> ⚠️ **Para alimentar Databricks y el tablero se usa la versión completa del notebook** (repositorio privado del equipo), que
> trabaja con los valores reales. Esta versión pública publica en el Volume resultados escalados y anonimizados, así que no
> debe correrse dentro del workspace del proyecto.

## Cómo ejecutarlo

```bash
pip install -r requirements.txt
cp .env.ejemplo .env      # opcional: llenar las credenciales para leer de Databricks
jupyter lab Pronostico_energia_EAFIT.ipynb     # o abrirlo en VS Code / Colab
```

- `MODO_RAPIDO = False` (valor por defecto, el de los resultados entregados): búsqueda completa de Optuna (25 pruebas), 12
  reentrenamientos y 83 emisiones simuladas en el test. Tarda **≈ 25-30 minutos** en un servidor de 4 núcleos.
- `MODO_RAPIDO = True` (celda de configuración): 8 pruebas de Optuna, solo para depurar; los números cambian ligeramente y
  **no** son los reportados.
- La celda 2 imprime las versiones de las librerías realmente usadas.

## Resumen del enfoque

- **Tarea:** regresión supervisada (forecasting tabular). A las 00:00 se pronostican los kWh comprados a EPM en cada una de las
  24 horas del día, para 9 subestaciones, con un único modelo global y objetivo normalizado por el nivel de los últimos 7 días.
- **Limpieza causal:** el estado de cada hora depende solo de esa hora y de horas anteriores (comprobado con 83 cortes
  diarios). Nada se imputa; cada valor descartado queda registrado con su valor original y su motivo.
- **Sin fuga, desde el dato crudo:** la prueba reconstruye limpieza + variables + predicción con los datos recortados a las
  23:00 y también con el futuro destruido; deben dar exactamente lo mismo.
- **Calendario fijo:** festivos, periodos académicos y `puente` se calculan una sola vez y se usan igual en entrenamiento,
  validación y pronóstico.
- **Validación:** walk-forward (4 pliegues mensuales, abr-jul 2026) para elegir modelo y ajustar hiperparámetros con Optuna.
- **Test (1-ago a 22-oct-2026):** 83 emisiones a las 00:00, reentrenando cada 7 días (12 reentrenamientos), con cobertura
  explícita: horas planeadas → con medición válida → comparables con la regla "semana pasada". Todos los métodos se comparan
  sobre las mismas filas.
- **Modelos comparados:** 4 baselines temporales, Ridge, Random Forest, LightGBM y un ensamble LightGBM + Random Forest. La
  regla de selección se fijó antes del test: menor WAPE de validación y, si dos candidatos están a menos de 0,1 puntos, el más
  simple. El ensamble gana en validación por solo 0,08 puntos, así que se elige **LightGBM ajustado** (en el test quedan
  empatados: 9,32 % vs 9,38 %).
- **Calendario:** 2026 según el cronograma oficial de pregrado; 2025 estimado trasladando 2026 52 semanas (mismo día de la
  semana) y comprobado con el consumo.
- **Modelo persistido:** se guardan los estimadores entrenados (`.joblib`) y su ficha, y se registra en MLflow; la sección
  13.1b comprueba que el modelo recargado del archivo pronostica exactamente igual.
- **Negocio:** todo valor en COP usa una tarifa **estimada** para el test (COP 524,02/kWh, promedio ene-jun 2026) y se describe
  como **reducción estimada del error de planeación**, no como ahorro en la factura. El umbral de alertas se elige en
  validación y se congela antes del test.

## Qué es confirmado y qué es hipótesis

Ver [`docs/supuestos_y_confirmaciones.md`](../docs/supuestos_y_confirmaciones.md).

## Qué no se ejecutó o queda abierto

Reentrenamiento diario (se reentrena cada 7 días), pronóstico meteorológico como entrada, agenda de eventos y generación solar.
Ver la sección 14.5 del notebook.
