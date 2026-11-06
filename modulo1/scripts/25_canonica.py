#!/usr/bin/env python3
"""Etapa 25. Ingesta de la base canónica: del paquete público a la zona interna y, por la compuerta, a la pública.

La base canónica (carpeta 02_canonica de SITECALI-Res) guarda lo observado en GeoParquet, Parquet y GeoTIFF por
tema, con un catálogo que dice la sensibilidad de cada columna, un inventario que dice la condición de cada archivo y
manifiestos con la huella SHA-256 de cada archivo. Esta etapa hace cinco cosas, en orden, según config/canonica.conf:

  1. Revisa el índice del paquete sin descomprimirlo: si trae una ruta prohibida (bóvedas reservadas, registros por
     persona, réplicas internas), se detiene y no copia nada.
  2. Lo descomprime en la zona interna (/srv/sitecali/interno/canonica, 0700) y reemplaza la copia anterior completa.
  3. Verifica cada archivo de datos contra la huella de los manifiestos de la canónica. Sin manifiesto o con huella
     distinta, el archivo no se publica.
  4. Compuerta por archivo y por columna: el archivo tiene que ser «abierta/pública» en el inventario y cada columna
     tiene que estar en el catálogo con sensibilidad pública. Lo que no está en el catálogo no sale. Escribe la zona
     pública: Parquet filtrado de todo lo que pasa, un GeoPackage con las tablas que van a PostGIS (prefijo can_),
     el diccionario de esas columnas y un reporte de lo omitido y por qué.
  5. Convierte los GeoTIFF verificados a COG con gdal_translate y recorta un año por comuna con gdalwarp.

GeoParquet se lee con geopandas y pyarrow: el GDAL 3.8.4 de Ubuntu 24.04 no trae el controlador de Parquet.
Uso: 25_canonica.py DATOS_DIR CONFIG_DIR INTERNO_DIR PUBLICO_DIR
"""
import configparser
import csv
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pyogrio

DATOS = ('.parquet', '.tif')


def lista(valor):
    return [v.strip() for v in re.split(r'[,\n]', valor) if v.strip()]


def huella(ruta):
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for bloque in iter(lambda: f.read(1 << 20), b''):
            h.update(bloque)
    return h.hexdigest()


def capa(ruta):
    """Nombre de la tabla publicada: can_ + ruta sin extensión, con / cambiado por _ (unidades/manzana → can_unidades_manzana)."""
    return 'can_' + re.sub(r'[^a-z0-9_]', '_', os.path.splitext(ruta)[0].lower().replace('/', '_'))


def leer_conf(conf_dir):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(os.path.join(conf_dir, 'canonica.conf'), encoding='utf-8')
    return cp


def paquete(datos_dir, patron):
    zips = sorted(glob.glob(os.path.join(datos_dir, patron)))
    if not zips:
        sys.exit(f'no hay en {datos_dir} un paquete {patron}; se arma con herramientas/armar_paquete_curso.ps1')
    return zips[-1]


def prohibidas(nombres, patrones):
    return [n for n in nombres if any(fnmatch.fnmatch(n.lower(), p.lower()) for p in patrones)]


def manifiestos(raiz):
    """archivo relativo → {sha256: manifiesto}, desde manifiesto_*.csv y el manifiesto JSON de los rásteres de 20 m.

    Un archivo puede figurar en dos manifiestos (por ejemplo, la afro corregida en el del bloque 1, con la versión
    anterior, y en el de la corrección por comuna). Se acepta si coincide con alguno, y el desacuerdo queda en el reporte.
    """
    m = {}
    for f in sorted(glob.glob(os.path.join(raiz, 'manifiesto_*.csv'))):
        with open(f, encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                if r.get('archivo') and r.get('sha256'):
                    m.setdefault(r['archivo'], {})[r['sha256'].lower()] = os.path.basename(f)
    for f in glob.glob(os.path.join(raiz, '**', 'manifiesto_*.json'), recursive=True):
        base = os.path.relpath(os.path.dirname(f), raiz).replace(os.sep, '/')
        for a in json.load(open(f, encoding='utf-8')).get('archivos', []):
            if a.get('archivo') and a.get('sha256'):
                m.setdefault(f"{base}/{a['archivo']}", {})[a['sha256'].lower()] = os.path.basename(f)
    return m


def catalogo(raiz):
    cat, desc = {}, {}
    for f in sorted(glob.glob(os.path.join(raiz, 'catalogo_canonica_bloque*.csv'))):
        with open(f, encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                cat.setdefault(r['archivo'], {})[r['columna']] = r['sensibilidad'].strip()
                desc[(r['archivo'], r['columna'])] = r.get('descripcion', '')
    return cat, desc


def inventario(raiz):
    rutas = sorted(glob.glob(os.path.join(raiz, 'inventario_canonica*.csv')))
    if not rutas:
        sys.exit('el paquete no trae inventario_canonica*.csv: sin la condición de cada archivo no se publica nada')
    with open(rutas[-1], encoding='utf-8') as fh:
        return {r['archivo']: r['condicion'].strip() for r in csv.DictReader(fh)}, os.path.basename(rutas[-1])


def main(datos_dir, conf_dir, interno, publico):
    cp = leer_conf(conf_dir)
    zpath = paquete(datos_dir, cp['paquete']['patron'])
    with zipfile.ZipFile(zpath) as z:
        # Los nombres se normalizan a «/»: un zip armado en Windows puede traer «\\» como separador.
        nombres = [n.replace('\\', '/') for n in z.namelist() if not n.endswith(('/', '\\'))]
        malos = prohibidas(nombres, lista(cp['paquete']['prohibidos']))
        if malos:
            sys.exit(f'el paquete trae material reservado o interno y no se descomprime: {malos[:5]}')
        print(f'paquete {os.path.basename(zpath)} (sha256 {huella(zpath)[:12]}…): {len(nombres)} archivos, '
              f'ninguno en rutas prohibidas')
        tmp = tempfile.mkdtemp(prefix='.canonica.', dir=interno)
        try:
            for info in z.infolist():
                nombre = info.filename.replace('\\', '/')
                if nombre.endswith('/'):
                    continue
                dst = os.path.normpath(os.path.join(tmp, nombre))
                if not dst.startswith(tmp + os.sep):
                    sys.exit(f'ruta fuera del destino en el paquete: {nombre}')
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(info) as fi, open(dst, 'wb') as fo:
                    shutil.copyfileobj(fi, fo)
            raiz_tmp = tmp
            if not glob.glob(os.path.join(tmp, 'catalogo_canonica_*.csv')) and os.path.isdir(os.path.join(tmp, '02_canonica')):
                raiz_tmp = os.path.join(tmp, '02_canonica')
            destino = os.path.join(interno, 'canonica')
            if os.path.exists(destino):
                shutil.rmtree(destino)
            os.replace(raiz_tmp, destino)
            os.chmod(destino, 0o700)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raiz = os.path.join(interno, 'canonica')

    man = manifiestos(raiz)
    fijadas = {k: v.strip().lower() for k, v in cp['huellas_fijadas'].items()} if cp.has_section('huellas_fijadas') else {}
    cat, desc = catalogo(raiz)
    inv, nombre_inv = inventario(raiz)
    datos = sorted(os.path.relpath(os.path.join(d, f), raiz).replace(os.sep, '/')
                   for d, _, fs in os.walk(raiz) for f in fs if f.endswith(DATOS))
    cond_pub = set(lista(cp['compuerta']['condiciones_publicas']))
    sens_pub = set(lista(cp['compuerta']['sensibilidades_publicas']))
    a_postgis = set(lista(cp['postgis']['tablas']))

    salida = os.path.join(publico, 'canonica')
    tmp_pub = tempfile.mkdtemp(prefix='.canonica.', dir=publico)
    reporte, campos, verificados, rasteres, n_fijadas = [], [], 0, [], 0
    gpkg = os.path.join(tmp_pub, 'canonica_publica.gpkg')
    try:
        for ruta in datos:
            fila = {'archivo': ruta, 'estado': 'omitido', 'motivo': '', 'columnas_publicadas': 0,
                    'columnas_omitidas': '', 'tabla_postgis': ''}
            esperadas = man.get(ruta, {})
            h = huella(os.path.join(raiz, ruta))
            nota = ''
            if h in esperadas:
                if len(esperadas) > 1:
                    nota = 'los manifiestos no coinciden entre sí; vale el de ' + esperadas[h]
            elif fijadas.get(ruta) == h:
                nota = 'huella fijada en config/canonica.conf (el manifiesto de la canónica está desactualizado)'
                n_fijadas += 1
            if h not in esperadas and fijadas.get(ruta) != h:
                fila['motivo'] = ('huella distinta de la del manifiesto' if esperadas
                                  else 'sin huella en los manifiestos de la canónica')
            elif inv.get(ruta) not in cond_pub:
                fila['motivo'] = f'condición en el inventario: {inv.get(ruta, "no figura")}'
            else:
                verificados += 1
                if ruta.endswith('.tif'):
                    rasteres.append(ruta)
                    fila.update(estado='publicado', motivo='ráster verificado; se convierte a COG')
                else:
                    publicar_parquet(raiz, ruta, cat, desc, sens_pub, a_postgis, tmp_pub, gpkg, fila, campos)
                if nota:
                    fila['motivo'] = (fila['motivo'] + '; ' if fila['motivo'] else '') + nota
            reporte.append(fila)

        cogs, recortes = convertir_rasteres(raiz, rasteres, cp['raster'], tmp_pub, set(r['archivo'] for r in reporte
                                                                                     if r['estado'] == 'publicado'))

        with open(os.path.join(tmp_pub, 'canonica.campos.csv'), 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['objeto', 'columna', 'tabla', 'campo', 'nivel_origen', 'naturaleza',
                                              'divulgacion', 'agregacion', 'descripcion'])
            w.writeheader()
            w.writerows(campos)
        with open(os.path.join(tmp_pub, 'canonica.reporte.csv'), 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(reporte[0]))
            w.writeheader()
            w.writerows(reporte)
        with open(os.path.join(tmp_pub, 'canonica.procedencia.json'), 'w', encoding='utf-8') as f:
            json.dump({'paquete': os.path.basename(zpath), 'sha256_paquete': huella(zpath), 'inventario': nombre_inv,
                       'archivos_verificados': verificados}, f, ensure_ascii=False, indent=1)
        if os.path.exists(salida):
            shutil.rmtree(salida)
        os.replace(tmp_pub, salida)
        for d, ds, fs in os.walk(salida):
            os.chmod(d, 0o755)
            for f in fs:
                os.chmod(os.path.join(d, f), 0o644)
    finally:
        shutil.rmtree(tmp_pub, ignore_errors=True)

    pub = [r for r in reporte if r['estado'] == 'publicado']
    omit = [r for r in reporte if r['estado'] != 'publicado']
    cols_omit = sum(len(r['columnas_omitidas'].split(';')) for r in pub if r['columnas_omitidas'])
    print(f'{verificados} de {len(datos)} archivos verificados ({n_fijadas} con huella fijada); {len(pub)} publicados, '
          f'{len(omit)} omitidos, {cols_omit} columnas retenidas por la compuerta')
    for r in omit:
        print(f'  omitido {r["archivo"]}: {r["motivo"]}')
    print(f'{sum(1 for r in pub if r["tabla_postgis"])} tablas para PostGIS (can_*), '
          f'{len(cogs)} GeoTIFF convertidos a COG, {recortes} recortes por comuna de {cp["raster"]["segmentar"]}')


def publicar_parquet(raiz, ruta, cat, desc, sens_pub, a_postgis, destino, gpkg, fila, campos):
    esquema = pq.read_schema(os.path.join(raiz, ruta))
    md = esquema.metadata or {}
    geo = json.loads(md[b'geo'])['primary_column'] if b'geo' in md else None
    c_cat = cat.get(ruta)
    if not c_cat:
        fila['motivo'] = 'el archivo no está en los catálogos de la canónica'
        return
    pasan, omitidas = [], []
    for c in esquema.names:
        if c == geo:
            continue
        s = c_cat.get(c)
        if s is None:
            omitidas.append(f'{c} (sin catálogo)')
        elif s not in sens_pub:
            omitidas.append(f'{c} ({s})')
        else:
            pasan.append(c)
    if not pasan:
        fila['motivo'] = 'ninguna columna pública en el catálogo'
        return
    leer = pasan + ([geo] if geo else [])
    if geo:
        df = gpd.read_parquet(os.path.join(raiz, ruta), columns=leer)
    else:
        df = pd.read_parquet(os.path.join(raiz, ruta), columns=leer)
    out = os.path.join(destino, 'parquet', ruta)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)
    fila.update(estado='publicado', motivo='', columnas_publicadas=len(pasan), columnas_omitidas='; '.join(omitidas))
    if ruta in a_postgis:
        nombre = capa(ruta)
        if geo:
            pyogrio.write_dataframe(df.rename_geometry('geom') if df.geometry.name != 'geom' else df, gpkg,
                                    layer=nombre, driver='GPKG')
        else:
            pyogrio.write_dataframe(pd.DataFrame(df), gpkg, layer=nombre, driver='GPKG')
        fila['tabla_postgis'] = nombre
        for c in pasan:
            campos.append({'objeto': nombre, 'columna': c, 'tabla': ruta, 'campo': c, 'nivel_origen': 'canónica',
                           'naturaleza': '', 'divulgacion': c_cat[c], 'agregacion': '',
                           'descripcion': desc.get((ruta, c), '')})


def convertir_rasteres(raiz, rasteres, conf, destino, publicados):
    cogs = []
    for ruta in rasteres:
        out = os.path.join(destino, 'cog', ruta)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run(['gdal_translate', '-q', '-of', 'COG', '-co', f'COMPRESS={conf.get("compresion", "DEFLATE")}',
                        '-co', 'BIGTIFF=IF_SAFER', os.path.join(raiz, ruta), out], check=True)
        cogs.append(out)
    seg = conf.get('segmentar')
    if not seg or seg not in rasteres:
        return cogs, 0
    if conf['unidades'] not in publicados:
        sys.exit(f'para segmentar hace falta {conf["unidades"]} publicado')
    u = gpd.read_parquet(os.path.join(raiz, conf['unidades']))
    if conf.get('solo_comunas', 'si') == 'si':
        u = u[u.comuna_num.notna()]
    tmp = tempfile.mkdtemp(prefix='.cortes.', dir=destino)
    d = os.path.join(destino, 'segmentos', os.path.splitext(os.path.basename(seg))[0])
    os.makedirs(d, exist_ok=True)
    n = 0
    try:
        for _, r in u.iterrows():
            etiqueta = re.sub(r'[^a-z0-9]+', '_', str(r[conf['campo_unidad']]).lower()).strip('_')
            corte = os.path.join(tmp, f'{etiqueta}.gpkg')
            gpd.GeoDataFrame([{'unidad': r[conf['campo_unidad']]}], geometry=[r.geometry], crs=u.crs).to_file(
                corte, driver='GPKG', engine='pyogrio')
            # La malla de 20 m está en EPSG:32618; gdalwarp lleva el polígono a ese sistema y recorta sin remuestrear.
            subprocess.run(['gdalwarp', '-q', '-overwrite', '-cutline', corte, '-crop_to_cutline', '-dstnodata', '-1',
                            '-of', 'COG', '-co', f'COMPRESS={conf.get("compresion", "DEFLATE")}',
                            os.path.join(raiz, seg), os.path.join(d, f'{etiqueta}.tif')], check=True)
            n += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return cogs, n


if __name__ == '__main__':
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
