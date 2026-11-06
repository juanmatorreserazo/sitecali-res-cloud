#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Ingesta completa del nodo interno: de la base de referencia y el paquete público de la canónica, ambos verificados
# por huella, a un esquema PostGIS público auditado.
#   sudo /sitecali-repo/modulo1/scripts/ingesta.sh
# Cada etapa corre como el usuario de servicio «sitecali» y escribe en /srv/sitecali/bitacora/ingesta.log.
# Es idempotente: correrla dos veces deja el mismo resultado y no duplica nada.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[ "$(id -u)" -eq 0 ] || [ "$(id -un)" = "sitecali" ] || { echo "corra la ingesta con sudo" >&2; exit 1; }
# La bitácora la crea el usuario de servicio, para que todas las etapas puedan escribir en ella.
como_sitecali touch "$BITACORA"

S="$SCRIPTS_DIR"
export SITECALI_REPO="$REPO" SITECALI_DATOS="$DATOS_DIR" SITECALI_DIR SITECALI_PY="$PY" SITECALI_PGDB="$PGDB"
a_bitacora() { while IFS= read -r l; do log "$l"; done; }

ETAPA="ingesta"
log "inicio · base v$BASE_VERSION · repositorio $REPO"
como_sitecali bash "$S/10_verificar_huellas.sh"
como_sitecali bash "$S/20_descomprimir_validar.sh"

ETAPA="25 canónica"
if ! como_sitecali "$PY" -I "$S/25_canonica.py" "$DATOS_DIR" "$REPO/config" "$INTERNO" "$PUBLICO" 2>&1 | a_bitacora; then
  falla "la ingesta de la base canónica se detuvo"
fi
como_sitecali bash "$S/30_marcos_publicos.sh"

ETAPA="35 transformar"
if ! como_sitecali "$PY" -I "$S/35_transformar.py" "$BASE" "$REPO/config" "$PUBLICO" 2>&1 | a_bitacora; then
  falla "la transformación de datos internos en patrones publicables se detuvo"
fi

ETAPA="40 capas"
if ! como_sitecali "$PY" -I "$S/40_capas_publicas.py" "$BASE" "$REPO/config" "$PUBLICO" 2>&1 | a_bitacora; then
  falla "la exportación de capas públicas se detuvo"
fi

ETAPA="60 auditoría gpkg"
if ! como_sitecali "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$REPO/config" \
       --gpkg "$PUBLICO"/marcos/*.gpkg "$PUBLICO/capas_publicas.gpkg" \
       --transformados "$PUBLICO/transformados.gpkg" --canonica "$PUBLICO/canonica" 2>&1 | a_bitacora; then
  falla "la auditoría encontró material no publicable en la zona pública; no se carga nada a PostGIS"
fi

como_sitecali bash "$S/50_cargar_postgis.sh"

ETAPA="60 auditoría postgis"
if ! como_sitecali "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$REPO/config" \
       --postgis "$PGDB" 2>&1 | a_bitacora; then
  falla "la auditoría encontró material no publicable en PostGIS"
fi

ETAPA="ingesta"
[ "$(huella "$BASE")" = "$BASE_GPKG_SHA256" ] || falla "la base cambió durante la ingesta"
log "fin · la base sigue intacta (sha256 ${BASE_GPKG_SHA256:0:12}…) · zona pública auditada"
