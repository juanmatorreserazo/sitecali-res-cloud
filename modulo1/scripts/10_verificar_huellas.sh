#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Etapa 10. Verifica sitecali.py y localiza el zip de la base por su huella, no por su nombre
# (una subida o una descarga pueden cambiar «.gpkg.zip» por «_gpkg.zip»). Deja la ruta en $INTERNO/.zip_base.
# Busca en datos/ y en 00_referencia/, que es donde la carpeta SITECALI-Res guarda la base congelada; Git ignora ambas.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
ETAPA="10 huellas"

[ "$(huella "$SITECALI_PY_SCRIPT")" = "$SITECALI_SHA256" ] \
  || falla "sitecali.py no coincide con la huella de config/base.conf; no se usa una herramienta alterada"
log "sitecali.py verificado"

# Si la base ya está descomprimida y verificada, el zip ya no hace falta.
if [ -f "$BASE" ] && [ "$(huella "$BASE")" = "$BASE_GPKG_SHA256" ]; then
  log "la base v$BASE_VERSION ya está en la zona interna con su huella; no se busca el zip"
  exit 0
fi

shopt -s nullglob
encontrado=""
for z in "$DATOS_DIR"/*.zip "$REPO"/00_referencia/*.zip; do
  if [ "$(huella "$z")" = "$BASE_ZIP_SHA256" ]; then encontrado="$z"; break; fi
done
[ -n "$encontrado" ] || falla "no hay en datos/ ni en 00_referencia/ un zip con la huella de la base v$BASE_VERSION ($BASE_ZIP_SHA256)"
echo "$encontrado" > "$INTERNO/.zip_base"
log "zip de la base v$BASE_VERSION verificado: $(basename "$encontrado")"
