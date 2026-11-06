#!/usr/bin/env bash
# PostGIS de la zona pública local. Solo existe el esquema «publico»: la zona interna no tiene tablas en PostGIS
# y sigue siendo el GeoPackage de /srv/sitecali/interno.
#
# Roles:
#   sitecali        dueño del esquema; entra por autenticación «peer» (usuario del sistema sitecali), sin clave
#   lector_publico  solo lectura sobre «publico»; es el que usarán GeoServer y la API en los módulos 2 y 3
set -euo pipefail

PGVER=16
CONF=/etc/postgresql/$PGVER/main

if [ -d /run/systemd/system ]; then
  systemctl enable --now postgresql
else
  pg_ctlcluster $PGVER main start 2>/dev/null || true
fi

como_postgres() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qAt "$@"; }

como_postgres <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sitecali') THEN CREATE ROLE sitecali LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_publico') THEN CREATE ROLE lector_publico LOGIN; END IF;
END $$;
SQL

if ! como_postgres -c "SELECT 1 FROM pg_database WHERE datname = 'sitecali'" | grep -q 1; then
  runuser -u postgres -- createdb -O sitecali sitecali
fi
# PostgreSQL concede CONNECT a PUBLIC en las bases del sistema; los roles del proyecto solo entran a «sitecali».
como_postgres -c "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC"

como_postgres -d sitecali <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS publico AUTHORIZATION sitecali;
-- En «public» quedan las funciones de PostGIS: el lector las usa (USAGE) pero nadie crea tablas ahí.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE sitecali FROM PUBLIC;
GRANT CONNECT ON DATABASE sitecali TO lector_publico;
GRANT USAGE ON SCHEMA publico TO lector_publico;
ALTER DEFAULT PRIVILEGES FOR ROLE sitecali IN SCHEMA publico GRANT SELECT ON TABLES TO lector_publico;
ALTER ROLE lector_publico SET search_path = publico, public;
ALTER ROLE sitecali SET search_path = publico, public;
SQL

# EPSG:9377 (Origen Nacional) no viene en el spatial_ref_sys de PostGIS 3.4.2 de Ubuntu 24.04 (comprobado el
# 26-10-2026). Sin esta fila, GDAL registraría un SRID propio y GeoServer no reconocería la proyección.
# La definición se toma de PROJ, la misma biblioteca que usan GDAL y PostGIS.
if [ "$(como_postgres -d sitecali -c 'SELECT count(*) FROM spatial_ref_sys WHERE srid = 9377')" = "0" ]; then
  como_postgres -d sitecali \
    -v wkt="$(gdalsrsinfo -o wkt1 --single-line EPSG:9377)" \
    -v p4="$(gdalsrsinfo -o proj4 EPSG:9377 | tr -d "'" | xargs)" <<'SQL'
INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text) VALUES (9377, 'EPSG', 9377, :'wkt', :'p4');
SQL
fi

# Clave del lector: se genera una vez y queda fuera del repositorio.
CLAVE=/srv/sitecali/credenciales/lector_publico.txt
if [ ! -s "$CLAVE" ]; then
  umask 077
  head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$CLAVE"
  chown root:sitecali "$CLAVE"; chmod 0640 "$CLAVE"
fi
como_postgres -c "ALTER ROLE lector_publico PASSWORD '$(cat "$CLAVE")'"

# Escucha en la VM para el puerto reenviado al anfitrión y para la red privada del Módulo 2.
# Solo lector_publico entra por red, con clave; sitecali solo por socket local.
if ! grep -q "^listen_addresses = '\*'" "$CONF/postgresql.conf"; then
  echo "listen_addresses = '*'" >> "$CONF/postgresql.conf"
fi
HBA="$CONF/pg_hba.conf"
if ! grep -q "sitecali-res" "$HBA"; then
  cat >> "$HBA" <<'HBA'
# sitecali-res: lector de solo lectura desde el anfitrión (NAT de VirtualBox) y desde la red privada
host  sitecali  lector_publico  10.0.2.2/32       scram-sha-256
host  sitecali  lector_publico  192.168.56.0/24   scram-sha-256
HBA
fi

if [ -d /run/systemd/system ]; then systemctl restart postgresql; else pg_ctlcluster $PGVER main restart; fi
echo "postgis listo: $(como_postgres -d sitecali -c 'SELECT postgis_full_version()' | cut -c1-60)…"
echo "EPSG:9377 en spatial_ref_sys: $(como_postgres -d sitecali -c 'SELECT count(*) FROM spatial_ref_sys WHERE srid = 9377')"
