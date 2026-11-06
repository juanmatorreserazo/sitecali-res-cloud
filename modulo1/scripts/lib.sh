#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Funciones y rutas comunes de la ingesta. Se carga con «source», no se ejecuta.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${SITECALI_REPO:-$(cd "$SCRIPTS_DIR/../.." && pwd)}"
DATOS_DIR="${SITECALI_DATOS:-$REPO/datos}"
SITECALI_DIR="${SITECALI_DIR:-/srv/sitecali}"
INTERNO="$SITECALI_DIR/interno"
PUBLICO="$SITECALI_DIR/publico"
BITACORA="$SITECALI_DIR/bitacora/ingesta.log"
PY="${SITECALI_PY:-/opt/sitecali-venv/bin/python}"
SITECALI_PY_SCRIPT="$REPO/herramientas/sitecali.py"
PGDB="${SITECALI_PGDB:-sitecali}"

# shellcheck source=../../config/base.conf
source "$REPO/config/base.conf"
BASE="$INTERNO/$BASE_GPKG_NOMBRE"

log()   { printf '%s  %-22s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "${ETAPA:-ingesta}" "$*" | tee -a "$BITACORA" >&2; }
falla() { log "ERROR: $*"; exit 1; }

huella() { sha256sum "$1" | cut -d' ' -f1; }

# Las etapas corren como el usuario de servicio. Si ya lo somos, no hace falta cambiar.
como_sitecali() {
  if [ "$(id -un)" = "sitecali" ]; then "$@"; else runuser -u sitecali -- "$@"; fi
}

# Lee un archivo de configuración «a|b|c» ignorando comentarios y líneas vacías.
leer_conf() { grep -v '^[[:space:]]*#' "$1" | grep -v '^[[:space:]]*$'; }
