# Job de Databricks

Aquí va la definición del Job que corre el pipeline completo: `01_bronze` → `02_silver` → `03_gold` → `04_resultados`.

**Cómo exportarlo:** Databricks → *Jobs & Pipelines* → abrir el Job → menú **⋮** → *View as code* (YAML) → copiar el contenido en
un archivo `pipeline_energia.yml` en esta carpeta.

Conviene incluir también un pantallazo de una ejecución exitosa (las cuatro tareas en verde), como evidencia de que el pipeline
corre automatizado.

*Estado: pendiente.*
