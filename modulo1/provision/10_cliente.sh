#!/usr/bin/env bash
# Máquina cliente: consume los servicios del servidor por la red privada y no guarda datos.
# Solo instala el cliente de PostgreSQL. net-tools y vim se instalan a mano en la práctica, como en la clase.
# Idempotente: se puede correr de nuevo con «vagrant provision cliente».
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends postgresql-client ca-certificates

if command -v timedatectl >/dev/null && [ -d /run/systemd/system ]; then
  timedatectl set-timezone America/Bogota
else
  ln -sf /usr/share/zoneinfo/America/Bogota /etc/localtime
fi
echo "cliente listo: $(psql --version)"
