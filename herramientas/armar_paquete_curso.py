#!/usr/bin/env python3
"""Equivalente en Python de armar_paquete_curso.ps1, para Linux o macOS (y para probar el paquete sin Windows).

Copia de 02_canonica solo los archivos con condición «abierta/pública» en el inventario más reciente, más catálogos,
manifiestos, pruebas, parámetros, el LEEME de la canónica, el manifiesto de los rásteres de 20 m y el inventario.
Nunca copia rutas prohibidas, aunque el inventario las marque como públicas.
Uso: armar_paquete_curso.py CARPETA_SITECALI-Res CARPETA_DESTINO
"""
import csv
import datetime as dt
import fnmatch
import glob
import hashlib
import os
import sys
import zipfile

PROHIBIDOS = ['eventos_reservado/*', 'educacion_reservado/*', '*_registros_*', '*replicas*', '*mayores_arreglos*',
              '*conteos_eventos*', '*simat_2025_*']


def main(origen, destino):
    can = os.path.join(origen, '02_canonica')
    inv = sorted(glob.glob(os.path.join(origen, '03_productos', 'base_sitecali', 'inventario_canonica_*.csv')))[-1]
    entradas = []
    with open(inv, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            ruta = r['archivo']
            if r['condicion'] != 'abierta/pública':
                continue
            if any(fnmatch.fnmatch(ruta.lower(), p) for p in PROHIBIDOS):
                print('retenido por ruta prohibida:', ruta)
                continue
            if not os.path.exists(os.path.join(can, ruta)):
                print('aviso: el inventario lista', ruta, 'pero no está en la carpeta')
                continue
            entradas.append((os.path.join(can, ruta), ruta))
    n_datos = len(entradas)
    for nombre in sorted(os.listdir(can)):
        if (nombre.startswith(('catalogo_canonica_', 'manifiesto_', 'pruebas_', 'parametros_')) or nombre == 'LEEME.md') \
                and os.path.isfile(os.path.join(can, nombre)):
            entradas.append((os.path.join(can, nombre), nombre))
    mob = os.path.join(can, 'ciudad', 'ob_20m', 'manifiesto_ob_20m.json')
    if os.path.exists(mob):
        entradas.append((mob, 'ciudad/ob_20m/manifiesto_ob_20m.json'))
    entradas.append((inv, os.path.basename(inv)))
    os.makedirs(destino, exist_ok=True)
    zpath = os.path.join(destino, f'canonica_publica_{dt.date.today():%Y%m%d}.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for src, nombre in entradas:
            z.write(src, nombre)
    h = hashlib.sha256(open(zpath, 'rb').read()).hexdigest()
    print(f'Inventario: {os.path.basename(inv)}')
    print(f'Archivos públicos copiados: {n_datos} y {len(entradas) - n_datos} documentos de la canónica')
    print(f'Paquete: {zpath} ({os.path.getsize(zpath) / 2**20:.1f} MB)')
    print(f'SHA256: {h.upper()}')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
