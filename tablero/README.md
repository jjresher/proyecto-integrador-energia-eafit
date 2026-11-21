# Tablero (SI7007)

*Estado: pendiente de subir.*

Aquí van:

- El archivo del tablero o su exportación (por ejemplo `.pbix` de Power BI, o la exportación del dashboard de Databricks).
- Las consultas SQL que usa cada visualización.
- Pantallazos de cada página, para verlo sin abrir la herramienta.

## Fuentes que debe usar

Las tablas Gold de `workspace.eafit_energia` (descritas en [`docs/arquitectura.md`](../docs/arquitectura.md#tablas-gold-que-produce-04_resultados)).
La principal es **`gold_real_vs_pronostico`**: cada hora del periodo de prueba con el consumo real, el pronóstico del modelo, la
regla "semana pasada", los errores y el costo estimado, por subestación. Para el pronóstico del día siguiente:
`gold_pronostico_emitido`. Para las métricas: `gold_metricas_test`.

## Qué evalúa la rúbrica

Argumentación visual en secuencia lógica, títulos que digan el hallazgo (no solo describan), ejes y anotaciones claras, filtros
y opciones de profundización (*drill-down*) para explorar de forma autónoma.
