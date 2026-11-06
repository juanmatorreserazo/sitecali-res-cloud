# Capacidades, observatorio y recorridos

27 de octubre de 2026. Este documento agrega al repositorio la línea positiva del proyecto: dónde ofrece la ciudad condiciones y oportunidades, qué activos tienen los hogares para usarlas y qué pasa cuando unos y otras no coinciden. Describe también el prototipo del observatorio y el diseño del componente de recorridos. Lo usan los módulos 3 y 4 del curso, y entra a la base central como el módulo `oportunidades_activos_2025` (prefijo `opa_`) del paquete de traspaso.

## Marco y construcción

El marco es el de activos, vulnerabilidad y estructura de oportunidades de Kaztman y Filgueira (CEPAL, 1999). La vulnerabilidad no se mide con un solo índice. Se mide como el desajuste entre los activos que el hogar controla y la estructura de oportunidades que ponen a su alcance el Estado, el mercado y la sociedad. Por eso `analisis/oportunidades_activos.py` construye dos índices separados.

La estructura de oportunidades tiene cinco dominios con igual peso: educación, salud, empleo alcanzable, espacio público y vida de barrio. Salen de los módulos públicos de oferta efectiva, espacial, acceso por franjas, accesibilidad al MIO y catastro. Los activos del hogar tienen tres dominios: capital humano, trabajo, y vivienda con conectividad. Salen de los agregados censales de 2018.

El universo son 13.280 manzanas residenciales de cabecera, ponderadas por sus residentes de 2018. Cada indicador se recorta en los percentiles 1 y 99, se estandariza con esos pesos y se orienta para que más sea mejor. Ese procedimiento sigue el Handbook OCDE-JRC (2008) y la documentación técnica del Child Opportunity Index 3.0.

El peso igual no es una comodidad, sino lo que los datos sostienen. El primer componente principal de los dominios explica 57 % de la varianza en oportunidades y 60 % en activos, con cargas equilibradas. El índice aritmético correlaciona entre 0,97 y 0,998 con las agregaciones geométrica y de componente principal.

Cada manzana lleva una clase de robustez. Es firme cuando las tres agregaciones la ponen en el mismo quintil, lo que ocurre para 78 % de los residentes en oportunidades y 72 % en activos.

Las privaciones se cuentan al estilo Alkire-Foster, el método del índice de pobreza multidimensional de Colombia. Un dominio está privado si cae en el 20 % inferior de la ciudad, y hay privación múltiple con dos o más. Esa privación alcanza a 24,6 % de los residentes, con intensidad 0,64 y M0 de 0,158.

## Lo que muestra el desajuste

Oportunidades y activos casi no se correlacionan en la ciudad (rho 0,04), y por eso un índice único habría escondido la mitad del fenómeno. El cruce por medianas ponderadas reparte a los residentes así:

| Situación | Activos del hogar | Oportunidades del entorno | Residentes |
| --- | --- | --- | --- |
| Integrada | Por encima de la mediana | Por encima de la mediana | 22 % |
| Activos sin oportunidades | Por encima | Por debajo | 13 % |
| Autoprovisión, solo en estratos 4 a 6 | Por encima | Por debajo | 14 % |
| Oportunidades sin activos | Por debajo | Por encima | 27 % |
| Exclusión acumulada | Por debajo | Por debajo | 22 % |

La autoprovisión se separa a propósito. En estratos 4 a 6, tener activos altos y poca oferta pública cerca indica que el hogar resuelve en el mercado privado, no que esté privado. Sin esa separación, 77 % de esos residentes caería como «activos sin oportunidades».

Dentro de los estratos 1 y 2 la población se parte en dos mitades casi iguales. Cerca de 41 % está en exclusión acumulada y cerca de 41 % tiene oportunidades cercanas que los hogares no alcanzan a usar. La primera mitad pide oferta y acceso; la segunda, política dirigida a las personas.

La tipología de hogar es descriptiva. Agrupa con k-medias (k = 6, semilla 20261027) las manzanas con 10 o más hogares según unipersonalidad, unipersonalidad de mayores, jefatura femenina, hogares con no parientes, hogares adicionales por vivienda y peso de niños y de mayores. Ninguno de esos rasgos entra a los índices como déficit.

## Observatorio

`observatorio/observatorio.html` es el prototipo de la cara pública del centro de pensamiento. Muestra un plano de las 15.658 manzanas con siete lentes: desajuste, oportunidades, activos, tipo de hogar, mecanismo de deterioro, desventaja física y social, y tasa de homicidio por barrio. Tiene además capas de tramos frontera y costura y de la rejilla adaptativa de homicidio, y una ficha por manzana que lee las cuatro lentes juntas.

Lee `datos.json`, que produce `observatorio/exportar_observatorio.py` solo a partir de marcos públicos, productos transformados y `opa_`. No hay puntos de eventos, fechas ni atributos de personas, y el índice interno de la fase 1 no aparece. En el módulo 3 el archivo estático se reemplaza por la API por manzana y por vector tiles.

## Recorridos

Los recorridos actualizan una ciudad que la base conoce por el censo de 2018 y llenan el dominio de sociedad que falta en las oportunidades. Los hacen juntas de acción comunal, organizaciones sociales, veedurías, ciudadanos y el propio equipo. Todos usan el mismo instrumento para que las observaciones sean comparables.

La unidad es la cara de manzana, como en la observación social sistemática de Sampson y Raudenbush. La muestra combina una selección dirigida (tramos frontera, exclusión acumulada, islas de deterioro y manzanas de robustez incierta) con una muestra de control al azar en todos los cuadrantes.

El instrumento registra la ciudad física, el dominio de sociedad y condiciones situacionales de seguridad. Nunca registra personas ni actividades ilegales. En sectores con fronteras invisibles se recorre de día, acompañados por la junta y sin fotografiar personas.

Google Street View queda para reconocer el terreno, planear rutas y enlazar desde la ficha. Sus términos prohíben crear contenido derivado de sus imágenes, con un ejemplo casi idéntico a este uso: un índice de árboles de una ciudad hecho con Street View. Por eso la codificación que se guarda se hace en campo o sobre imagen abierta de Mapillary (CC BY-SA). Lo que se capture con celular o cámara 360 se publica allí, y así puede analizarse con visión computacional en el módulo 4.

Antes de entrar a la base, todo pasa por una aduana. Allí 15 % de las caras se observa dos veces para medir el acuerdo entre canales, se quitan los metadatos de las fotos, se difuminan rostros y placas y los observadores quedan con seudónimo. Después se agrega con ecometría en un módulo `obs_` por cara de manzana, manzana y tramo, con fecha, canal y confiabilidad.

## Fuentes

[Kaztman y Filgueira (CEPAL, 1999)](https://repositorio.cepal.org/bitstream/handle/11362/28663/lcmvd176rev1_es.pdf?sequence=1) · [OECD/JRC, Handbook on Constructing Composite Indicators (2008)](https://knowledge4policy.ec.europa.eu/sites/default/files/jrc47008_handbook_final.pdf) · [Child Opportunity Index 3.0, documentación técnica](https://www.diversitydatakids.org/sites/default/files/file/COI30_TechDoc_20240313.pdf) · [Helbich et al., Health & Place (2024)](https://dspace.library.uu.nl/bitstream/handle/1874/437831/1-s2.0-S1353829224000728-main.pdf?sequence=1) · [Términos de Google Maps Platform (copia en Cesium)](https://cesium.com/legal/terms-for-google/) · [Mapillary, licencias](https://help.mapillary.com/hc/en-us/articles/115001770409-Licenses) · [Odgers et al. (2012)](https://sampson.scholars.harvard.edu/publications/systematic-social-observation-children%E2%80%99s-neighborhoods-using-google-street-view)
