#!/usr/bin/env bash
# Pruebas de la compuerta de divulgación. Cada prueba altera a propósito una copia temporal de la configuración
# (nunca la real) y exige que la etapa correspondiente se niegue, con el mensaje esperado.
#   sudo /sitecali-repo/modulo1/scripts/pruebas_compuerta.sh
# Requiere una ingesta previa (la base en la zona interna y el esquema «publico» cargado).
source "$(dirname "$0")/lib.sh"
set +e   # lib.sh activa -e; aquí los rechazos son el resultado esperado, no un error

[ -f "$BASE" ] || { echo "primero corra ingesta.sh" >&2; exit 1; }
S="$SCRIPTS_DIR"
TMP="$(mktemp -d)"; chmod 0755 "$TMP"
limpiar() {
  rm -rf "$TMP"
  for t in prueba_fuga prueba_celda prueba_fecha can_referencia_calendario_2016_2027; do
    como_sitecali psql -X -qAt -d "$PGDB" -c "DROP TABLE IF EXISTS publico.$t" \
      -c "DELETE FROM publico.procedencia WHERE objeto = '$t'" >/dev/null 2>&1
  done
}
trap limpiar EXIT

reiniciar() {  # copia limpia del repositorio de prueba (config y herramienta)
  rm -rf "$TMP/config" "$TMP/herramientas"
  cp -r "$REPO/config" "$REPO/herramientas" "$TMP/"
  chmod -R a+rX "$TMP"
}
pasan=0; fallan=0
esperar_rechazo() {  # descripción, patrón esperado en la salida, comando...
  local desc="$1" patron="$2"; shift 2
  local salida codigo
  salida="$("$@" 2>&1)"; codigo=$?
  if [ $codigo -ne 0 ] && grep -q -- "$patron" <<<"$salida"; then
    printf 'ok      %s\n' "$desc"; pasan=$((pasan + 1))
  else
    printf 'FALLA   %s (código %s)\n%s\n' "$desc" "$codigo" "$salida" | head -5; fallan=$((fallan + 1))
  fi
}
como_prueba() { como_sitecali env SITECALI_REPO="$TMP" SITECALI_DATOS="$DATOS_DIR" SITECALI_DIR="$SITECALI_DIR" \
                  SITECALI_PY="$PY" SITECALI_PGDB="$PGDB" "$@"; }
# Las salidas de las pruebas van a un destino temporal: la zona pública real no se toca.
DEST="$TMP/destino"; mkdir -p "$DEST"; chown sitecali "$DEST"

reiniciar; echo "vial_lesionados|@publicas|vial_lesionados" >> "$TMP/config/capas_publicas.conf"
esperar_rechazo "capa excluida pedida como pública (vial_lesionados)" "capas_excluidas.conf" \
  como_prueba "$PY" -I "$S/40_capas_publicas.py" "$BASE" "$TMP/config" "$DEST"

reiniciar; sed -i '/^homicidios/d' "$TMP/config/capas_excluidas.conf"
echo "homicidios|@publicas|homicidios" >> "$TMP/config/capas_publicas.conf"
esperar_rechazo "puntos de víctimas aun sin la lista de exclusiones (homicidios)" "geometría no es pública" \
  como_prueba "$PY" -I "$S/40_capas_publicas.py" "$BASE" "$TMP/config" "$DEST"

reiniciar; sed -i 's/^det_islas|@publicas/det_islas|isla,hom_2018_2024/' "$TMP/config/capas_publicas.conf"
esperar_rechazo "columna interna pedida por nombre (det_islas.hom_2018_2024)" "no son públicas" \
  como_prueba "$PY" -I "$S/40_capas_publicas.py" "$BASE" "$TMP/config" "$DEST"

reiniciar; echo "fuga|cmpa_*|2024" >> "$TMP/config/marcos_publicos.conf"
esperar_rechazo "marco con un módulo interno (cmpa_)" "uso interno" \
  como_prueba bash "$S/30_marcos_publicos.sh"

reiniciar; echo "# alterado" >> "$TMP/herramientas/sitecali.py"
esperar_rechazo "herramienta alterada (sitecali.py)" "no coincide con la huella" \
  como_prueba bash "$S/10_verificar_huellas.sh"

reiniciar
como_sitecali psql -X -qAt -d "$PGDB" -c \
  "CREATE TABLE publico.prueba_fuga AS SELECT cod_dane_a, 1 AS hmz_n FROM publico.manzanas_geom LIMIT 3" >/dev/null
esperar_rechazo "tabla plantada en PostGIS por fuera de la ingesta" "prefijo de módulo interno" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" --postgis "$PGDB"
como_sitecali psql -X -qAt -d "$PGDB" -c "DROP TABLE publico.prueba_fuga" >/dev/null

reiniciar; sed -i 's/^k = 5$/k = 2/' "$TMP/config/transformaciones.conf"
como_prueba "$PY" -I "$S/35_transformar.py" "$BASE" "$TMP/config" "$DEST" >/dev/null 2>&1
esperar_rechazo "k bajado a 2 en la configuración: la auditoría mantiene el piso de 5" "valores entre 1 y 4" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" \
    --gpkg "$PUBLICO/capas_publicas.gpkg" --transformados "$DEST/transformados.gpkg"

reiniciar
plantar() {  # tabla, select; la registra como producto transformado
  como_sitecali psql -X -qAt -d "$PGDB" -c "CREATE TABLE publico.$1 AS $2" \
    -c "INSERT INTO publico.procedencia (objeto, tipo, archivo, sha256_archivo, version_base, sha256_base, sha256_sitecali)
        VALUES ('$1', 'transformado', 'prueba', 'x', 'x', 'x', 'x')" >/dev/null
}
retirar() { como_sitecali psql -X -qAt -d "$PGDB" -c "DROP TABLE IF EXISTS publico.$1" \
              -c "DELETE FROM publico.procedencia WHERE objeto = '$1'" >/dev/null; }
plantar prueba_celda "SELECT 3 AS n, 'x' AS periodo"
esperar_rechazo "celda transformada con 3 eventos plantada en PostGIS" "valores entre 1 y 4" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" --postgis "$PGDB"
retirar prueba_celda
plantar prueba_fecha "SELECT 12 AS n, DATE '2024-03-01' AS fecha_hecho"
esperar_rechazo "producto transformado con una fecha plantado en PostGIS" "atributo individual" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" --postgis "$PGDB"
retirar prueba_fecha

# Base canónica (etapa 25). Las pruebas usan paquetes y salidas de prueba en $TMP: la zona interna y la pública
# reales no se tocan.
reiniciar
CAN="$TMP/canonica"; mkdir -p "$CAN/datos" "$CAN/interno" "$CAN/publico"; chown -R sitecali "$CAN"
como_sitecali "$PY" -c '
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("LEEME.md", "paquete de prueba")
    z.writestr("eventos_reservado/homicidios_victimas_2018_2024.parquet", b"no importa el contenido")
' "$CAN/datos/canonica_publica_20990101.zip"
esperar_rechazo "paquete de la canónica con un archivo de una bóveda reservada" "material reservado" \
  como_prueba "$PY" -I "$S/25_canonica.py" "$CAN/datos" "$TMP/config" "$CAN/interno" "$CAN/publico"

plantar_parquet() {  # ruta relativa, columna extra; deja una salida pública de prueba con esa columna
  rm -rf "$CAN/salida"; mkdir -p "$CAN/salida"; chown sitecali "$CAN/salida"
  como_sitecali "$PY" -c '
import os, sys, pandas as pd
ruta = os.path.join(sys.argv[1], "parquet", sys.argv[2]); os.makedirs(os.path.dirname(ruta), exist_ok=True)
pd.DataFrame({"COD_DANE_A": ["7600100000000000000000"], sys.argv[3]: [1]}).to_parquet(ruta)
' "$CAN/salida" "$1" "$2"
}
plantar_parquet referencia/calendario_2016_2027.parquet festivo
esperar_rechazo "columna interna de la canónica en la zona pública (calendario.festivo)" "sensibilidad «interno»" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" \
    --gpkg "$PUBLICO/capas_publicas.gpkg" --canonica "$CAN/salida"
plantar_parquet unidades/manzana.parquet columna_nueva
esperar_rechazo "columna sin catálogo de la canónica en la zona pública" "sin catálogo" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" \
    --gpkg "$PUBLICO/capas_publicas.gpkg" --canonica "$CAN/salida"
plantar can_referencia_calendario_2016_2027 "SELECT 2024 AS anio, 1 AS festivo"
como_sitecali psql -X -qAt -d "$PGDB" -c "UPDATE publico.procedencia SET tipo = 'canonica'
  WHERE objeto = 'can_referencia_calendario_2016_2027'" >/dev/null
esperar_rechazo "tabla can_* con una columna interna plantada en PostGIS" "sensibilidad «interno»" \
  como_prueba "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$TMP/config" --postgis "$PGDB"
retirar can_referencia_calendario_2016_2027

if como_sitecali "$PY" -I "$S/60_auditar_divulgacion.py" --base "$BASE" --conf "$REPO/config" --postgis "$PGDB" >/dev/null 2>&1; then
  echo "ok      el esquema público real sigue limpio después de las pruebas"; pasan=$((pasan + 1))
else
  echo "FALLA   el esquema público real no pasa la auditoría"; fallan=$((fallan + 1))
fi
huerfanos=("$PUBLICO"/.marcos.* "$PUBLICO"/.capas.* "$PUBLICO"/.transformados.* "$PUBLICO"/.canonica.* "$INTERNO"/.canonica.*)
if [ ! -e "${huerfanos[0]}" ] && [ ! -e "${huerfanos[1]}" ] && [ ! -e "${huerfanos[2]}" ] \
   && [ ! -e "${huerfanos[3]}" ] && [ ! -e "${huerfanos[4]}" ]; then
  echo "ok      sin temporales huérfanos en la zona pública"; pasan=$((pasan + 1))
else
  echo "FALLA   quedaron temporales en $PUBLICO"; fallan=$((fallan + 1))
fi

echo "$pasan pruebas pasan, $fallan fallan"
[ "$fallan" -eq 0 ]
