#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Etapa 30. Primera compuerta: arma los marcos públicos por manzana con «sitecali.py marco --publico», que se
# niega a exportar campos internos y vacía los condicionados donde la manzana no es publicable.
# Cada marco deja también su diccionario de campos (<nombre>.campos.csv).
set -euo pipefail
source "$(dirname "$0")/lib.sh"
ETAPA="30 marcos"

destino="$PUBLICO/marcos"
tmp="$(mktemp -d "$PUBLICO/.marcos.XXXX")"
trap 'rm -rf "$tmp"' EXIT

while IFS='|' read -r nombre campos vigencia; do
  args=(marco "$BASE" --campos "$campos" --nivel manzana --publico -o "$tmp/$nombre.gpkg")
  [ "$vigencia" != "-" ] && args+=(--vigencia "$vigencia")
  salida="$("$PY" -I "$SITECALI_PY_SCRIPT" "${args[@]}" 2>&1)" || falla "marco $nombre rechazado: $salida"
  log "$nombre: $(printf '%s\n' "$salida" | head -1)"
done < <(leer_conf "$REPO/config/marcos_publicos.conf")

# Se reemplaza el conjunto completo solo si todos los marcos salieron.
rm -f "$destino"/*.gpkg "$destino"/*.campos.csv
mv "$tmp"/*.gpkg "$tmp"/*.campos.csv "$destino"/
chmod 0644 "$destino"/*
log "$(ls "$destino"/*.gpkg | wc -l) marcos públicos en $destino"
