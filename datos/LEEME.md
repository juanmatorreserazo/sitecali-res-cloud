# Carpeta de datos (no se versiona)

Aquí va el paquete público de la base canónica, `canonica_publica_AAAAMMDD.zip`, que arma `herramientas/armar_paquete_curso.ps1` desde la carpeta SITECALI-Res. Su huella cambia con la fecha; la etapa 25 verifica cada archivo del paquete contra los manifiestos de la canónica. Si hay varios paquetes, usa el de fecha más reciente.

La base de referencia 2.4.113 (`1_base_integrada_cali_v24113.gpkg.zip`, 172 MB) puede estar aquí o en `00_referencia/`. El nombre no importa, porque la etapa 10 la identifica por su huella SHA-256, que está en `config/base.conf`. Git ignora todo lo que haya en esta carpeta salvo este archivo.

Dentro de la VM, esta carpeta se ve de solo lectura en `/sitecali-repo/datos`. La ingesta copia la base y la canónica al disco propio de la VM (`/srv/sitecali/interno`) y nunca las abre desde la carpeta compartida.

Nunca van aquí el paquete portátil de `armar_paquete_portatil.bat` (trae archivos internos), el zip de lo reservado ni los paquetes internos del traspaso.
