# Visor por barrio · SITECALI-Res

Es el mismo producto que cada módulo del curso despliega de una manera distinta. En el Módulo 2 lo sirven los contenedores Apache del clúster LXD, detrás de HAProxy.

## Qué hay aquí

`exportar_visor.py` lee el corte público que el Módulo 1 dejó en PostGIS (esquema `publico`, rol `lector_publico`) y escribe `datos.js`: un registro por barrio con su geometría simplificada y el valor de cada lente. Solo usa la biblioteca estándar de Python y `psql`. Se detiene si encuentra un conteo entre 1 y 4.

`sitio/index.html` es la página. Carga `datos.js`, que debe estar en la misma carpeta, y no necesita nada más. La marca `__SERVIDOR__` que trae en la etiqueta `<body>` se reemplaza en cada contenedor por su nombre, y así la página dice qué servidor la entregó.

## Reglas

`datos.js` no se sube al repositorio: se genera cada vez desde la base. El archivo `.gitignore` ya lo excluye.

Una lente nueva entra a la lista `OPCIONALES` del exportador solo después de confirmar en el catálogo qué mide la columna y en qué sentido se lee.

El visor muestra barrios, nunca personas ni eventos. Las tasas suprimidas en el corte público salen como suprimidas.

## Cómo se genera y se despliega

Los comandos completos, en orden, están en la Parte E del documento «Paso a paso · Módulo 2».
