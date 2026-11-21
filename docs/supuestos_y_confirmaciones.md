# Supuestos y confirmaciones del área técnica

Lo que se sabe de los datos, separado según qué tan seguro es. Toda decisión del pipeline y del modelo se apoya en esta tabla.
Las subestaciones aparecen con su código anónimo (SUB1 a SUB9), igual que en el notebook público.

## Confirmado por el área técnica de la universidad

| Tema | Respuesta | Cómo se usa |
|---|---|---|
| Qué miden las series | Energía **comprada a EPM** (kWh por hora). La generación solar se consume en el momento, así que la compra queda neta de lo que producen los paneles | El objetivo del modelo es la compra a la red |
| Medidor general | Es el **total de la universidad**; coincide aproximadamente con la suma de las nueve series | Solo para conciliar; la suma de las 9 series explica ≈ 95 % del medidor general |
| Relación subestación ↔ inventario | Se confirmó qué fila del inventario corresponde a cada serie (y por tanto su capacidad) | Regla de capacidad física (R2). La relación y las capacidades no se publican |
| SUB9 | Es una carga de **climatización** | No se le aplica la regla de pico aislado (arranca de golpe a las 6:00) |
| SUB3 | Cambió transformador y medidor (nuevo régimen desde el 5-mar-2026) | Las lecturas anteriores se tratan como otro tramo del medidor |
| Huecos de SUB1 y SUB3 | No son recuperables (cambios de equipo y obras) | No se rellenan |
| Valor de SUB8 el 21-feb-2026 (cientos de veces su nivel normal) | Falla de señal, no consumo | Los valores imposibles, los ceros y los nulos se limpian |
| Generación solar horaria | No existe registro horario | El efecto del sol solo se infiere con la radiación de NASA POWER |

## Hipótesis sin confirmar

- Transferencia de carga de SUB9 a SUB3 en abril de 2026 (coincide en el tiempo con el silencio de SUB9, pero no hay registro
  de la maniobra).
- Causa exacta de la diferencia entre la suma de las nueve series y el medidor general (otras subestaciones, pérdidas o
  diferencias de medición).

## Estimado por el equipo

- **Calendario académico de 2025:** el de 2026 trasladado 52 semanas (mismo día de la semana), con la Semana Santa real y el
  cierre de fin de año según la caída del consumo. Comprobado con el consumo. El de 2026 es el oficial.
- **Tarifa de agosto a octubre de 2026:** COP 524,02/kWh, promedio de enero a junio de 2026. Por eso todo valor en pesos del
  periodo de prueba se presenta como **estimado**.

## No disponible

- Agenda de eventos del campus: ningún día atípico se llama "evento confirmado".
- Pronósticos meteorológicos históricos: el clima solo se probó como observado (día anterior) y como "oráculo".

## Ajuste frente a la propuesta

La propuesta citaba un consumo mensual del campus menor que el real: según el medidor general, el consumo de los meses
completos de 2026 es cerca del doble de esa cifra (ver la conciliación en el notebook del modelo, sección 3.3).
