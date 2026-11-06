#!/usr/bin/env bash
# Paquetes del sistema, usuario de servicio y estructura de zonas del nodo interno.
# Idempotente: se puede correr de nuevo con «vagrant provision --provision-with sistema».
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  gdal-bin unzip ca-certificates \
  postgresql-16 postgresql-16-postgis-3 \
  python3-venv python3-pip \
  acl htop tree

# Hora de Cali en la bitácora (si el sistema lo permite; en contenedores de prueba puede no haber systemd).
if command -v timedatectl >/dev/null && [ -d /run/systemd/system ]; then
  timedatectl set-timezone America/Bogota
else
  ln -sf /usr/share/zoneinfo/America/Bogota /etc/localtime
fi

# Usuario de servicio sin shell interactiva. La ingesta corre con este usuario, no con root ni con vagrant.
if ! id sitecali >/dev/null 2>&1; then
  useradd --system --home-dir /srv/sitecali --shell /usr/sbin/nologin sitecali
fi

# Zonas. «interno» guarda la base completa: solo el usuario sitecali la lee (0700).
# «publico» guarda lo que salió por la compuerta y puede leerlo cualquiera en la VM (0755).
install -d -o sitecali -g sitecali -m 0755 /srv/sitecali
install -d -o sitecali -g sitecali -m 0700 /srv/sitecali/interno
install -d -o sitecali -g sitecali -m 0755 /srv/sitecali/publico /srv/sitecali/publico/marcos
install -d -o sitecali -g sitecali -m 0750 /srv/sitecali/bitacora
install -d -o root     -g sitecali -m 0750 /srv/sitecali/credenciales

echo "sistema listo: $(ogrinfo --version), $(psql --version)"
