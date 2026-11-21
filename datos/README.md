# Datos

El repositorio es **público**. Los archivos que entregó la Universidad EAFIT **no se suben** (el `.gitignore` los excluye):
son datos internos de la operación del campus. Aquí solo están los datos públicos y la descripción de cada fuente.

## Fuentes

| Archivo | Qué contiene | Origen | ¿En el repositorio? |
|---|---|---|---|
| `Subestaciones_15_abril_2025_-_22_oct_2026.xlsx` | Energía comprada a la red (kWh) **hora a hora** de 9 subestaciones del campus Medellín, del 15-abr-2025 al 22-oct-2026. Una hoja por subestación (fecha, hora y kWh) y una hoja `Resumen` con los totales mensuales. 13 344 horas × 9 series | Universidad EAFIT | ❌ No |
| `consumo_total.xlsx` | Medidor general de la universidad, por hora, ago-2025 a ago-2026 (solo se usa para conciliar) | Universidad EAFIT | ❌ No |
| `Subestaciones_EAFIT.xlsx` | Inventario de subestaciones: capacidad del transformador y bloques que atiende cada una | Universidad EAFIT | ❌ No |
| `privado.json` | Archivo del equipo para la versión pública: código anónimo de cada subestación (SUB1-SUB9), capacidad de cada transformador y el factor con el que se escalan kWh y COP | Equipo del proyecto | ❌ No |
| [`clima_nasa_power_eafit.csv`](clima_nasa_power_eafit.csv) | Clima horario en las coordenadas del campus (6,2006 N; −75,5783 O), 1-abr-2025 a 31-oct-2026: temperatura (`T2M`, °C), humedad (`RH2M`, %), precipitación (`PRECTOTCORR`, mm), radiación solar (`ALLSKY_SFC_SW_DWN` y `CLRSKY_SFC_SW_DWN`, W/m²). Horas locales | [NASA POWER](https://power.larc.nasa.gov/) (datos públicos), API horaria, comunidad RE | ✅ Sí |
| [`ref_calendario_academico.csv`](ref_calendario_academico.csv) | Periodos académicos sin traslapes (clases, evaluaciones finales, receso, intersemestral, vacaciones) con su fuente: 2026 según el cronograma oficial de pregrado; 2025 estimado trasladando 2026 52 semanas | Cronograma oficial EAFIT 2026 (público) y estimación del equipo | ✅ Sí |

## Para ejecutar el proyecto

- **Databricks:** subir los archivos de EAFIT y los públicos al Volume de entrada del pipeline (lo hace la capa Bronze; ver
  [`databricks/`](../databricks/)).
- **Notebook público del modelo** (el de [`modelo/`](../modelo/)): poner en **esta carpeta** los tres Excel de EAFIT y
  `privado.json`. Sin `privado.json` el notebook se detiene con un mensaje, porque es el que anonimiza y escala los datos. Git
  no sube ninguno de esos archivos aunque estén aquí.
- Los archivos de EAFIT y `privado.json` se le piden al equipo del proyecto; están en su repositorio privado.

## Lo que se sabe de los datos

Supuestos y respuestas confirmadas por el área técnica de la universidad (qué mide cada serie, errores conocidos, cambios de
equipo): ver [`docs/supuestos_y_confirmaciones.md`](../docs/supuestos_y_confirmaciones.md).
