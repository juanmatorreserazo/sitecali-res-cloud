#!/usr/bin/env python3
"""Etapa 40. Segunda compuerta: exporta las capas con geometría que figuran en config/capas_publicas.conf.

Reglas, en este orden:
  1. Lista de permitidos. Lo que no está en capas_publicas.conf no sale.
  2. La lista de exclusiones (capas_excluidas.conf) prevalece sobre la de permitidos.
  3. Solo salen columnas clasificadas «publico» en sc_campos. Con «@publicas» las demás se omiten y quedan en el
     reporte; con una lista explícita, una columna no pública detiene la exportación.
  4. La geometría sale solo si sc_campos la clasifica como «publico».
Toda la configuración se valida antes de exportar la primera capa: un error no deja nada a medias.

Salidas en la zona pública: capas_publicas.gpkg, capas_publicas.campos.csv (diccionario, con el mismo formato
de los marcos más la columna «objeto») y capas_publicas.reporte.csv (filas, columnas publicadas, omitidas y sin
descripción).
Uso: 40_capas_publicas.py BASE.gpkg CONFIG_DIR DESTINO_DIR
"""
import csv
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def leer_conf(ruta):
    with open(ruta, encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith('#'):
                yield [p.strip() for p in linea.split('|')]


def planificar(con, conf_dir):
    """Resuelve cada capa pedida en (capa, nombre, geom, elegidas, omitidas, catálogo) o se detiene."""
    excluidas = {p[0]: p[1] for p in leer_conf(os.path.join(conf_dir, 'capas_excluidas.conf'))}
    features = {r[0]: r[1] for r in con.execute(
        "SELECT c.table_name, g.column_name FROM gpkg_contents c "
        "JOIN gpkg_geometry_columns g USING (table_name) WHERE c.data_type = 'features'")}
    plan, nombres = [], set()
    for capa, columnas, nombre in leer_conf(os.path.join(conf_dir, 'capas_publicas.conf')):
        if capa in excluidas:
            sys.exit(f'{capa} está en capas_excluidas.conf ({excluidas[capa]}) y también en la lista de permitidos: '
                     'corrija la configuración')
        if capa not in features:
            sys.exit(f'{capa} no es una capa con geometría de la base')
        if nombre in nombres:
            sys.exit(f'el nombre de salida {nombre} está repetido')
        nombres.add(nombre)
        geom = features[capa]
        cat = {r[0]: r[1:] for r in con.execute(
            'SELECT campo, divulgacion, naturaleza, descripcion FROM sc_campos WHERE tabla = ?', (capa,))}
        if cat.get(geom, ('',))[0] != 'publico':
            sys.exit(f'{capa}: la geometría no es pública en sc_campos; la capa no sale')
        en_tabla = [r[1] for r in con.execute(f'PRAGMA table_info("{capa}")') if r[1] not in (geom, 'fid')]
        if columnas == '@publicas':
            elegidas = [c for c in en_tabla if cat.get(c, ('',))[0] == 'publico']
        else:
            elegidas = [c.strip() for c in columnas.split(',')]
            malas = [c for c in elegidas if cat.get(c, ('',))[0] != 'publico']
            if malas:
                sys.exit(f'{capa}: estas columnas no son públicas en sc_campos y no salen: {malas}')
        if not elegidas:
            sys.exit(f'{capa}: no quedó ninguna columna pública')
        omitidas = [c for c in en_tabla if c not in elegidas]
        plan.append((capa, nombre, geom, elegidas, omitidas, cat))
    return plan


def main(base, conf_dir, destino):
    con = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    plan = planificar(con, conf_dir)

    tmp = tempfile.mkdtemp(prefix='.capas.', dir=destino)
    try:
        salida = os.path.join(tmp, 'capas_publicas.gpkg')
        diccionario, reporte = [], []
        for capa, nombre, geom, elegidas, omitidas, cat in plan:
            sql = 'SELECT ' + ', '.join(f'"{c}"' for c in elegidas + [geom]) + f' FROM "{capa}"'
            cmd = ['ogr2ogr', '-f', 'GPKG', salida, base, '-sql', sql, '-nln', nombre,
                   '-lco', 'GEOMETRY_NAME=geom', '-a_srs', 'EPSG:9377']
            if os.path.exists(salida):
                cmd.append('-update')
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f'ogr2ogr falló en {capa}: {r.stderr.strip()}')
            filas = con.execute(f'SELECT count(*) FROM "{capa}"').fetchone()[0]
            nivel = (con.execute('SELECT nivel FROM sc_tablas WHERE tabla = ?', (capa,)).fetchone() or ('',))[0]
            sin_desc = [c for c in elegidas if not (cat[c][2] or '').strip()]
            for c in elegidas:
                div, nat, desc = cat[c]
                diccionario.append({'objeto': nombre, 'columna': c, 'tabla': capa, 'campo': c, 'nivel_origen': nivel,
                                    'naturaleza': nat, 'divulgacion': div, 'agregacion': '',
                                    'descripcion': desc or ''})
            reporte.append({'capa': capa, 'salida': nombre, 'filas': filas, 'publicadas': len(elegidas),
                            'omitidas': len(omitidas), 'sin_descripcion': len(sin_desc),
                            'omitidas_detalle': ' '.join(f'{c}({cat.get(c, ("sin_catalogo",))[0]})'
                                                         for c in omitidas)})
            print(f'{nombre}: {filas} filas, {len(elegidas)} columnas públicas, {len(omitidas)} omitidas, '
                  f'{len(sin_desc)} sin descripción')

        for nombre_csv, filas in (('capas_publicas.campos.csv', diccionario),
                                  ('capas_publicas.reporte.csv', reporte)):
            with open(os.path.join(tmp, nombre_csv), 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=list(filas[0]))
                w.writeheader()
                w.writerows(filas)

        for archivo in os.listdir(tmp):
            os.replace(os.path.join(tmp, archivo), os.path.join(destino, archivo))
            os.chmod(os.path.join(destino, archivo), 0o644)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
