# SITECALI-Res Cloud Platform

Repositorio del curso de computación en la nube de la Especialización en IA (UAO), construido sobre la base SITECALI-Res y la línea de investigación sobre deterioro y polaridades socioespaciales en Cali. Desde el 5 de noviembre de 2026 usa dos entradas: la base integrada 2.4.113, congelada como referencia, y el paquete público de la base canónica, que reorganizó la base por temas en GeoParquet y GeoTIFF. El curso no parte de un problema de volumen: lo publicable de la base ocupa unos 100 MB en PostGIS. Parte de dos problemas reales de la base. Uno es la gobernanza de la divulgación, porque 503 campos internos conviven en el mismo archivo con los públicos. El otro es la intensidad de cómputo de la inferencia bajo incertidumbre: réplicas, nulos espaciales, permutaciones y cadenas MCMC.

La arquitectura tiene dos zonas. La zona interna es el nodo local de este módulo: guarda la base completa y es el único lugar donde viven los módulos internos y los modelos bayesianos. La zona pública, primero en PostGIS local y desde el Módulo 3 en Azure, recibe lo que pasa por cuatro puertas. Tres son compuertas: `sitecali.py marco --publico` para los atributos por manzana, una lista de permitidos para las capas con geometría y, para la canónica, una lista de permitidos doble que exige que el inventario marque el archivo como público y que el catálogo declare pública cada columna. La tercera es una transformación: los datos internos (puntos de homicidio, lesionados, incidentes y visitas de riesgo) salen convertidos en patrón (rejillas adaptativas, esquinas, tasas suavizadas, fronteras y corredores), nunca como registro individual. Después corre una auditoría independiente sobre lo que llegó al destino.

## Estado

El Módulo 1 está completo y probado en Ubuntu 24.04 con Python 3.12.3 (el mismo sistema de la VM), con la base 2.4.113 y el paquete público de la canónica del 5 de noviembre de 2026. Comprende el aprovisionamiento y la ingesta idempotente, con la canónica verificada por manifiesto y filtrada por catálogo. Sigue la transformación de datos internos en patrones publicables, que reproduce exactamente los módulos públicos de la base central. Termina con la carga a PostGIS con procedencia y diccionario, la auditoría y quince pruebas de la compuerta. Cómo encaja la canónica en las dos zonas y en los módulos 2 a 4 está en `docs/arquitectura_canonica.md`. `analisis/oportunidades_activos.py` y el exportador del observatorio todavía leen módulos de la v2.4 que la 2.4.113 marca como reemplazados, y quedan por adaptar. Los módulos 2, 3 y 4 se construyen sobre el esquema `publico` que deja este módulo. Desde el 27 de octubre el repositorio trae también la línea de capacidades (índices de oportunidades y activos del hogar, desajuste, privaciones y tipología de hogar), el prototipo del observatorio público y el diseño del componente de recorridos, descritos en `docs/capacidades_y_recorridos.md`.

```
config/                  base.conf (versión y huellas), canonica.conf, marcos, capas, exclusiones, transformaciones, requirements
datos/                   el paquete público de la canónica (Git lo ignora)
00_referencia/           la base 2.4.113 congelada, copiada de SITECALI-Res (Git la ignora)
herramientas/            sitecali.py (verificado por huella antes de cada uso) y armar_paquete_curso.ps1 / .py
modulo1/Vagrantfile      servidor (nodo interno, Ubuntu 24.04, 8 GB, 4 vCPU) y cliente (1 GB, 1 vCPU)
modulo1/provision/       sistema y zonas, entorno de Python, PostGIS y roles
modulo1/scripts/         ingesta.sh, etapas 10 a 60 (25 = canónica, 35 = transformar), pruebas_compuerta.sh
modulo1/docs/            decisiones y hallazgos; transformaciones y resultados de resolución y bordes
analisis/                oportunidades_activos.py: índices de capacidades (módulo opa_ de la base)
observatorio/            prototipo público y exportar_observatorio.py, que genera su datos.json
docs/                    arquitectura de la canónica, capacidades, observatorio y recorridos
```

El observatorio se regenera así, desde la base y los resultados del paquete de traspaso:

```
python3 analisis/oportunidades_activos.py BASE.gpkg opa.csv
python3 observatorio/exportar_observatorio.py BASE.gpkg transformados.gpkg modulos/ opa.csv observatorio/datos.json
```

## Requisitos del anfitrión

El equipo de referencia es un Intel Core i7-1165G7 (4 núcleos y 8 hilos) con 16 GB. La VM toma 8 GB y 4 vCPU, lo que deja unos 8 GB al sistema anfitrión. Hacen falta VirtualBox y Vagrant en versiones recientes y compatibles entre sí: cada versión de Vagrant declara qué versiones de VirtualBox admite, y una VirtualBox más nueva que la que Vagrant conoce falla al arrancar la VM. La virtualización (VT-x) tiene que estar activa en la BIOS.

En Windows hay dos cosas que conviene revisar antes del primer arranque. La primera son los finales de línea: el archivo `.gitattributes` obliga a usar LF, pero si el repositorio se clonó antes de que existiera, hay que volver a clonarlo, porque un script con CRLF falla dentro de la VM con `$'\r': command not found`. La segunda es el hipervisor: si Hyper-V, WSL2, la Plataforma de máquina virtual o la integridad de memoria (aislamiento del núcleo) están activos, VirtualBox corre sobre el hipervisor de Windows y es bastante más lento, y en la barra de estado de la VM aparece el ícono de una tortuga. Desactivarlos mejora el rendimiento, pero quitar la integridad de memoria reduce una protección del sistema: es una decisión que conviene tomar con esa información.

## Uso

```
# 1. Copiar el zip de la base 2.4.113 a 00_referencia/ o a datos/ (el nombre da igual; se identifica por huella)
# 2. Armar el paquete público de la canónica (en Windows; deja datos\canonica_publica_AAAAMMDD.zip)
powershell -ExecutionPolicy Bypass -File .\herramientas\armar_paquete_curso.ps1
# 3. Levantar y aprovisionar el servidor (nodo interno) y el cliente
cd modulo1
vagrant up

# 4. Ingesta completa: huellas, validación, canónica, marcos, transformaciones, capas, auditoría, PostGIS, auditoría
vagrant ssh servidor -c "sudo /sitecali-repo/modulo1/scripts/ingesta.sh"

# 5. Demostrar que la compuerta se cierra (quince pruebas)
vagrant ssh servidor -c "sudo /sitecali-repo/modulo1/scripts/pruebas_compuerta.sh"
```

La bitácora queda en `/srv/sitecali/bitacora/ingesta.log`. En la prueba de referencia del 5 de noviembre (Ubuntu 24.04 con 2 vCPU), la ingesta completa tardó unos tres minutos: medio minuto para validar la base, medio minuto para la canónica (verificación, compuerta, 8 COG y 22 recortes por comuna) y casi un minuto para las transformaciones. Una segunda corrida dejó los mismos conteos. En la VM el tiempo puede variar con el disco y con el hipervisor del anfitrión. Desde el anfitrión, QGIS se conecta a PostGIS en `127.0.0.1:15432`, base `sitecali`, usuario `lector_publico`. La clave se genera en la VM y no viaja en el repositorio; se lee con `vagrant ssh servidor -c "sudo cat /srv/sitecali/credenciales/lector_publico.txt"`. El puerto solo escucha en la interfaz local del anfitrión, no en la red. La máquina cliente (192.168.56.11) llega al mismo PostGIS por la red privada en 192.168.56.10:5432, con el mismo usuario de solo lectura, y no guarda datos.

## Qué queda en PostGIS

El esquema `publico` recibe trece marcos por manzana de la base 2.4.113, con los módulos recalculados en la v2.5: físico y barrera, acceso y franjas, oferta efectiva, territorio oficial, población de referencia 2018–2027, desventaja, mecanismos de deterioro (censal, v3 y estricto v4), educación del hogar, espacio público, bordes y rejilla de homicidio por manzana. Son tablas de atributos sin geometría y con llave `cod_dane_a` (y `vigencia` donde aplica). Recibe también doce capas con geometría en EPSG:9377: la geometría de las 15.658 manzanas, secciones de deterioro, islas, barrios, comunas, UPU, sedes educativas, salud, espacio público efectivo, escenarios deportivos, cobertura boscosa y jerarquía vial. Recibe además trece productos transformados, descritos en `modulo1/docs/transformaciones.md`: rejillas adaptativas de homicidio, incidentes y lesionados, las esquinas de siniestralidad, las tasas de homicidio por barrio y por sección con intervalo, las visitas de riesgo por barrio, las fronteras entre barrios y entre manzanas con sus pruebas, los corredores estructurantes y el registro de cada transformación. Recibe además diecinueve tablas de la base canónica con el prefijo `can_`, entre ellas las manzanas con su universo (`can_unidades_manzana`), la población de referencia 2018–2027, las medidas físicas y de acceso de 2025, los conteos censales de 2005 y 2018, el catastro y la edificación satelital por manzana y año. Las acompañan `procedencia`, con la versión y la huella de la base o del paquete de la canónica y de cada archivo cargado, y `diccionario`, con la descripción de cada columna publicada. Fuera de PostGIS, la zona pública guarda la canónica filtrada en Parquet y los GeoTIFF de edificación como COG, con un año recortado por comuna. Los nombres de columna quedan en minúsculas, que es la convención de PostgreSQL.

Para agregar un marco, una capa o una transformación, o para cambiar una resolución o un umbral, se edita el archivo correspondiente de `config/` y se vuelve a correr la ingesta: todo se rehace desde los datos originales, sin heredar resultados anteriores. Lo que se quita de la configuración también se retira de PostGIS en la siguiente corrida.
