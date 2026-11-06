#!/usr/bin/env bash
# Entorno de Python para sitecali.py y los scripts de la compuerta, aislado del Python del sistema.
set -euo pipefail

REPO="${SITECALI_REPO:-/sitecali-repo}"
VENV=/opt/sitecali-venv
# En Ubuntu 24.04 «python3» es la 3.12. SITECALI_PYTHON permite fijar otro intérprete.
PYTHON="${SITECALI_PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO/config/requirements.txt"
"$VENV/bin/python" -c "import numpy, pandas, geopandas, pyogrio; import sys; print('python listo:', sys.version.split()[0], '· pandas', pandas.__version__, '· geopandas', geopandas.__version__)"
