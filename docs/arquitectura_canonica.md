# La base canónica en la arquitectura del curso

5 de noviembre de 2026. Desde el 3 de noviembre la base de SITECALI-Res ya no es un solo GeoPackage. La carpeta de trabajo de JuanMa se organiza en tres capas: las fuentes tal como llegaron, la canónica y los productos. La canónica guarda solo lo observado y lo que sale de ello por una regla fija, en GeoParquet, Parquet y GeoTIFF por tema. Los productos (índices, modelos, matrices de entrenamiento y piezas públicas) nunca vuelven a ella. Este documento explica cómo encaja esa organización en las dos zonas del repositorio y qué consecuencias tiene para los módulos 2 a 4.

## Las cuatro carpetas leídas como zonas

`00_referencia` guarda la base integrada 2.4.113 congelada, con su catálogo de 5.414 campos, su manifiesto y `sitecali.py`. No se modifica y sirve para reproducir cualquier cifra ya publicada. En el curso es la base de referencia de `config/base.conf`: de ella salen los marcos por manzana, las capas heredadas y, sobre todo, los productos públicos que ya fueron transformados (rejillas, esquinas, tasas suavizadas, tramos y bordes), porque la canónica no guarda productos. La ingesta la busca por su huella en `datos/` y en `00_referencia/`, y Git ignora las dos carpetas.

`01_fuentes` no entra al curso. Allí están los archivos tal como llegaron, incluidos los microdatos censales y los registros reservados, y nada de esa carpeta tiene por qué estar en la máquina virtual.

`02_canonica` es la base de trabajo, y entra al curso solo en su parte pública. La organización trae lo que la compuerta necesitaba y antes había que deducir: un inventario que dice la condición de cada archivo (abierta y pública, publicable con regla, interna o reservada), un catálogo por bloque que dice la sensibilidad de cada columna y manifiestos con la huella SHA-256 de cada archivo. Las dos bóvedas reservadas (`eventos_reservado` y `educacion_reservado`) son carpetas aparte, de modo que separar lo reservado es una operación sobre rutas y no sobre el contenido.

`03_productos` tampoco entra al curso por ahora. Lo público de allí se publicará como producto con su propia versión. Las matrices de entrenamiento de `03_productos/matrices` llevan conteos de eventos por manzana y son de uso interno.

## Cómo pasa la canónica por las dos zonas

El paso empieza fuera de la máquina virtual. `herramientas/armar_paquete_curso.ps1`, o su equivalente `armar_paquete_curso.py` en Linux, lee el inventario más reciente y copia a `datos/canonica_publica_AAAAMMDD.zip` solo los archivos con condición «abierta/pública». Agrega catálogos, manifiestos, pruebas, parámetros y el inventario mismo. Una lista de rutas prohibidas retiene las bóvedas, los registros por persona, las réplicas, los arreglos de personas mayores y los conteos de eventos aunque el inventario los marcara como públicos por error. Este paquete no es el portátil que arma `armar_paquete_portatil.bat`: el portátil excluye lo reservado, pero conserva archivos internos como las réplicas de población y los arreglos de las personas mayores.

Dentro de la máquina, la etapa 25 repite el control por su cuenta. Lee el índice del zip antes de descomprimir y se detiene si encuentra una ruta prohibida. Después copia el paquete a la zona interna y verifica cada archivo contra los manifiestos de la canónica; la huella del zip cambia con la fecha y no se fija en ninguna parte. Por último aplica una compuerta doble. Un archivo sale si el inventario lo marca como público, y de ese archivo sale solo cada columna que el catálogo declara pública. Lo que no figura en el catálogo no sale.

El resultado va a la zona pública en tres formas. Una es el Parquet filtrado de todo lo que pasó, que es lo que en el Módulo 3 irá a un almacenamiento de objetos en la nube. Otra es un GeoPackage con las tablas que se cargan a PostGIS, y la tercera son los GeoTIFF convertidos a COG, más un año de edificación recortado por comuna. La auditoría independiente lee los catálogos de la canónica desde la zona interna y revisa lo que efectivamente llegó.

Las tablas de la canónica van al mismo esquema `publico` de PostGIS, con el prefijo `can_`. Un esquema aparte habría obligado a repetir los permisos del lector y a volver a aprovisionar la máquina; con el prefijo, el origen de cada tabla queda a la vista y los permisos, la procedencia, el diccionario y la auditoría ya existentes la cubren sin cambios.

## Lo que esto cambia en los módulos siguientes

En el Módulo 2, el clúster LXD sirve con GeoServer las capas `can_*` y los marcos de la base de referencia. Los COG de edificación se pueden servir como cobertura sin convertirlos, porque el formato ya está pensado para leerse por partes. Las pruebas de JMeter consultan manzanas por atributos que ahora tienen fuente y catálogo verificables.

En el Módulo 3, el Parquet filtrado es la materia prima natural de la API por manzana y del servidor de teselas: se consulta con DuckDB o pyarrow sin cargarlo entero en memoria, y su lugar en la nube es un contenedor de almacenamiento de objetos con los COG al lado. La regla de la canónica de guardar conteos y no porcentajes pasa a la API: los indicadores se calculan al responder, con las fórmulas documentadas, en lugar de copiarse.

En el Módulo 4 hay una restricción que conviene fijar desde ahora: el microproyecto 3 no puede entrenar en Azure Machine Learning con las matrices de `03_productos/matrices`, porque son internas. La agenda de modelos de la hoja de ruta de SITECALI-Res ofrece dos candidatos que solo usan fuentes públicas. Uno es el uso de cada edificación (residencial, industrial, comercial o de equipamiento), aprendido de la forma de la huella satelital de Open Buildings con etiquetas del catastro de Cali y de los municipios vecinos. El otro es la población por manzana después de 2018, estimada con la edificación anual, el catastro y la matrícula. El primero encaja además con el componente de visión computacional del módulo.

## Deudas de la canónica que la ingesta hizo visibles

La primera prueba con el paquete del 5 de noviembre verificó 117 de los 119 archivos públicos y publicó 108. Los tres extractos de OpenStreetMap más recientes, las cuatro tablas de la serie del EDUC y los dos archivos de intervalos no tienen catálogo, y por eso no salen. El relieve y su sombreado tienen una huella distinta de la que dice su manifiesto. El manifiesto de los rásteres de edificación de 20 m dice versión c6.5, pero su extensión y sus huellas son de una malla anterior; mientras la canónica no lo regenere, esos ocho archivos se verifican con huellas fijadas en `config/canonica.conf`. La afro corregida figura en dos manifiestos con huellas distintas, y vale la del manifiesto de la corrección por comuna. Ninguna de estas deudas es un error de la ingesta, que hace lo que debe al no publicar lo que no puede verificar o no tiene descrito. Corregirlas en la canónica las publica en la siguiente corrida, sin tocar el código.
