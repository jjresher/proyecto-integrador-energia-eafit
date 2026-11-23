# Tablero (SI7007)

Tablero en **Power BI**, «Energía EAFIT», con las 9 subestaciones (SUB1 a SUB9, cada una con su bloque) y un filtro por
subestación. Cada página responde una pregunta, en orden, y la responde en su primera tarjeta («Respuesta»):

| Pestaña | Tipo | Pregunta que responde |
|---|---|---|
| Resumen | Pregunta principal | ¿Conviene adoptar el pronóstico LightGBM? |
| 1 · Contexto | Descriptiva | ¿Cuándo consume más energía el campus? |
| 2 · Precisión | Descriptiva | ¿Qué porcentaje falla el pronóstico en cada hora? |
| 3 · Dónde falla | Diagnóstica | ¿Dónde supera el error la meta del 10 %? |
| 4 · Valor | Diagnóstica y negocio | ¿Cuánto mejora el presupuesto diario de energía? |
| 5 · Mañana | Predictiva | ¿Cuánta energía se espera el viernes 23-oct-2026? |
| Ficha de KPIs | Referencia | ¿Cómo se calcula cada indicador? |

Las capturas de cada página muestran consumos reales y por eso están solo en el repositorio privado del equipo.

## Fuentes

Tablas Gold de `workspace.eafit_energia`: `gold_real_vs_pronostico` (cada hora del periodo de prueba con real, pronóstico,
regla y errores), `gold_pronostico_emitido` (el día siguiente), `gold_metricas_test` y las tablas de `03_gold` para el
contexto. *Pendiente de confirmar con quien lo construyó: conexión directa al SQL Warehouse o extracto.*
