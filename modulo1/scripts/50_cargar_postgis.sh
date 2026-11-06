#!/usr/bin/env bash
# shellcheck disable=SC2034  # ETAPA y las rutas comunes las usa log() en lib.sh
# Etapa 50. Carga a PostGIS (esquema «publico») lo que salió por las compuertas, con su procedencia.
#   Marcos      → tablas de atributos sin geometría (una fila por manzana, o por manzana y vigencia)
#   Capas       → tablas con geometría en EPSG:9377 e índice espacial GIST
#   Canónica    → tablas can_* de la base canónica que pasaron la compuerta de la etapa 25 (mismo esquema y mismos
#                 permisos del lector; el prefijo dice de dónde vienen)
#   Diccionario → publico.diccionario, la descripción de cada columna publicada
# Los nombres de columna quedan en minúsculas (convención de PostgreSQL): COD_DANE_A → cod_dane_a.
# Se ejecuta como el usuario de servicio «sitecali», que entra a PostgreSQL por «peer», sin clave.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
ETAPA="50 postgis"

PG="PG:dbname=$PGDB"
psql_() { PGOPTIONS='-c client_min_messages=warning' psql -X -v ON_ERROR_STOP=1 -qAt -d "$PGDB" "$@"; }
SHA_BASE="$(huella "$BASE")"
SHA_HERR="$(huella "$SITECALI_PY_SCRIPT")"
INICIO="$(psql_ -c 'SELECT clock_timestamp()')"

psql_ <<'SQL'
CREATE TABLE IF NOT EXISTS publico.procedencia (
  objeto           text PRIMARY KEY,
  tipo             text NOT NULL,
  archivo          text NOT NULL,
  sha256_archivo   text NOT NULL,
  version_base     text NOT NULL,
  sha256_base      text NOT NULL,
  sha256_sitecali  text NOT NULL,
  filas            bigint,
  columnas         integer,
  cargado_en       timestamptz NOT NULL DEFAULT now()
);
DROP TABLE IF EXISTS publico.diccionario;
CREATE TABLE publico.diccionario (
  objeto text, columna text, columna_pg text, tabla_origen text, campo_origen text, nivel_origen text,
  naturaleza text, divulgacion text, agregacion text, descripcion text,
  PRIMARY KEY (objeto, columna_pg)
);
SQL

registrar() {  # objeto tipo archivo [versión huella_origen]
  local objeto="$1" tipo="$2" archivo="$3" vb="${4:-$BASE_VERSION}" sb="${5:-$SHA_BASE}" filas columnas
  filas="$(psql_ -c "SELECT count(*) FROM publico.\"$objeto\"")"
  columnas="$(psql_ -c "SELECT count(*) FROM information_schema.columns WHERE table_schema='publico' AND table_name='$objeto'")"
  psql_ -v o="$objeto" -v t="$tipo" -v a="$(basename "$archivo")" -v sa="$(huella "$archivo")" \
        -v vb="$vb" -v sb="$sb" -v sh="$SHA_HERR" -v f="$filas" -v c="$columnas" <<'SQL'
INSERT INTO publico.procedencia (objeto, tipo, archivo, sha256_archivo, version_base, sha256_base, sha256_sitecali, filas, columnas)
VALUES (:'o', :'t', :'a', :'sa', :'vb', :'sb', :'sh', :'f', :'c')
ON CONFLICT (objeto) DO UPDATE SET tipo = EXCLUDED.tipo, archivo = EXCLUDED.archivo,
  sha256_archivo = EXCLUDED.sha256_archivo, version_base = EXCLUDED.version_base, sha256_base = EXCLUDED.sha256_base,
  sha256_sitecali = EXCLUDED.sha256_sitecali, filas = EXCLUDED.filas, columnas = EXCLUDED.columnas, cargado_en = now();
SELECT format('COMMENT ON TABLE publico.%I IS %L', :'o',
  format('SITECALI-Res v%s · %s · %s · sha256 %s', :'vb', :'t', :'a', left(:'sa', 12))) \gexec
SQL
  log "$objeto: $filas filas, $columnas columnas"
}

cargar_diccionario() {  # objeto csv
  psql_ -v o="$1" <<SQL
CREATE TEMP TABLE d (columna text, tabla text, campo text, nivel_origen text, naturaleza text,
                     divulgacion text, agregacion text, descripcion text);
\copy d FROM '$2' WITH (FORMAT csv, HEADER true)
INSERT INTO publico.diccionario
SELECT :'o', columna, lower(columna), tabla, campo, nivel_origen, naturaleza, divulgacion, agregacion, descripcion FROM d;
SQL
}

# Marcos: atributos por manzana, sin geometría (la geometría vive una sola vez en manzanas_geom).
for g in "$PUBLICO"/marcos/*.gpkg; do
  objeto="marco_$(basename "$g" .gpkg)"
  ogr2ogr -f PostgreSQL "$PG" "$g" marco -nln "publico.$objeto" -nlt NONE -overwrite -lco FID=fid
  if psql_ -c "SELECT 1 FROM information_schema.columns WHERE table_schema='publico' AND table_name='$objeto' AND column_name='vigencia'" | grep -q 1; then
    psql_ -c "CREATE UNIQUE INDEX ON publico.\"$objeto\" (cod_dane_a, vigencia)"
  else
    psql_ -c "CREATE UNIQUE INDEX ON publico.\"$objeto\" (cod_dane_a)"
  fi
  registrar "$objeto" marco "$g"
  cargar_diccionario "$objeto" "${g%.gpkg}.campos.csv"
done

# Capas con geometría.
CAPAS="$PUBLICO/capas_publicas.gpkg"
for capa in $(ogrinfo -q -so "$CAPAS" 2>/dev/null | sed -E 's/^[0-9]+: ([^ ]+).*/\1/'); do
  ogr2ogr -f PostgreSQL "$PG" "$CAPAS" "$capa" -nln "publico.$capa" -overwrite \
          -lco FID=fid -lco GEOMETRY_NAME=geom -lco SPATIAL_INDEX=GIST
  registrar "$capa" capa "$CAPAS"
done
psql_ -c "CREATE UNIQUE INDEX IF NOT EXISTS manzanas_geom_cod ON publico.manzanas_geom (cod_dane_a)"

cargar_diccionario_objetos() {  # csv con columna «objeto» (capas y transformados)
  psql_ <<SQL
CREATE TEMP TABLE d (objeto text, columna text, tabla text, campo text, nivel_origen text, naturaleza text,
                     divulgacion text, agregacion text, descripcion text);
\copy d FROM '$1' WITH (FORMAT csv, HEADER true)
INSERT INTO publico.diccionario
SELECT objeto, columna, lower(columna), tabla, campo, nivel_origen, naturaleza, divulgacion, agregacion, descripcion FROM d;
SQL
}
cargar_diccionario_objetos "$PUBLICO/capas_publicas.campos.csv"

# Productos transformados (etapa 35): rejillas, esquinas, tasas, conteos, fronteras y corredores.
TRANS="$PUBLICO/transformados.gpkg"
if [ -f "$TRANS" ]; then
  for capa in $(ogrinfo -q -so "$TRANS" 2>/dev/null | sed -E 's/^[0-9]+: ([^ ]+).*/\1/'); do
    ogr2ogr -f PostgreSQL "$PG" "$TRANS" "$capa" -nln "publico.$capa" -overwrite \
            -lco FID=fid -lco GEOMETRY_NAME=geom -lco SPATIAL_INDEX=GIST
    registrar "$capa" transformado "$TRANS"
  done
  cargar_diccionario_objetos "$PUBLICO/transformados.campos.csv"
fi

# Base canónica (etapa 25): tablas can_* con la versión del paquete y su huella como procedencia.
CANON="$PUBLICO/canonica/canonica_publica.gpkg"
if [ -f "$CANON" ]; then
  PAQ="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["paquete"], d["sha256_paquete"])' \
         "$PUBLICO/canonica/canonica.procedencia.json")"
  for capa in $(ogrinfo -q -so "$CANON" 2>/dev/null | sed -E 's/^[0-9]+: ([^ ]+).*/\1/'); do
    ogr2ogr -f PostgreSQL "$PG" "$CANON" "$capa" -nln "publico.$capa" -overwrite -nlt PROMOTE_TO_MULTI \
            -lco FID=fid -lco GEOMETRY_NAME=geom -lco SPATIAL_INDEX=GIST
    registrar "$capa" canonica "$CANON" "canónica · ${PAQ% *}" "${PAQ#* }"
  done
  cargar_diccionario_objetos "$PUBLICO/canonica/canonica.campos.csv"
fi

# Lo que ya no se publica no se queda: se borran las tablas de «publico» que esta carga no registró.
for viejo in $(psql_ -v ini="$INICIO" -c "SELECT tablename FROM pg_tables WHERE schemaname='publico'
                         AND tablename NOT IN ('procedencia','diccionario')
                         AND tablename NOT IN (SELECT objeto FROM publico.procedencia WHERE cargado_en >= '$INICIO')"); do
  psql_ -c "DROP TABLE publico.\"$viejo\"" && psql_ -c "DELETE FROM publico.procedencia WHERE objeto = '$viejo'"
  log "retirada la tabla $viejo, que ya no está en la configuración"
done

psql_ <<'SQL'
SELECT format('ANALYZE publico.%I', tablename) FROM pg_tables WHERE schemaname = 'publico' \gexec
SQL
log "carga terminada: $(psql_ -c "SELECT count(*) FROM publico.procedencia") objetos, $(psql_ -c "SELECT count(*) FROM publico.diccionario") columnas en el diccionario"
