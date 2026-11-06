#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Etapa 20. Descomprime la base al disco propio de la VM (zona interna), verifica la huella del gpkg y corre
# «sitecali.py validar», que debe terminar en «sin errores». Idempotente: si la base ya está, solo valida.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
ETAPA="20 descomprimir"

if [ -f "$BASE" ] && [ "$(huella "$BASE")" = "$BASE_GPKG_SHA256" ]; then
  log "base presente y verificada: $BASE"
else
  zip="$(cat "$INTERNO/.zip_base")"
  tmp="$(mktemp -d "$INTERNO/.descomprimiendo.XXXX")"
  trap 'rm -rf "$tmp"' EXIT
  log "descomprimiendo $(basename "$zip") en la zona interna"
  unzip -q "$zip" "$BASE_GPKG_NOMBRE" -d "$tmp"
  [ "$(huella "$tmp/$BASE_GPKG_NOMBRE")" = "$BASE_GPKG_SHA256" ] || falla "el gpkg descomprimido no tiene la huella esperada"
  mv "$tmp/$BASE_GPKG_NOMBRE" "$BASE"
  chmod 0600 "$BASE"
  log "base v$BASE_VERSION instalada ($(du -h "$BASE" | cut -f1)), permisos 0600"
fi

ETAPA="20 validar"
salida="$("$PY" -I "$SITECALI_PY_SCRIPT" validar "$BASE" 2>&1)" || falla "sitecali.py validar falló: $salida"
printf '%s\n' "$salida" | while IFS= read -r l; do log "$l"; done
[ "$(printf '%s\n' "$salida" | tail -1)" = "sin errores" ] || falla "la validación no terminó en «sin errores»"
