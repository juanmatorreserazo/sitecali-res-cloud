#!/usr/bin/env python3
"""Etapa 60. Auditoría de divulgación sobre lo que llegó al destino, no sobre lo que se pidió.

Es independiente de las compuertas: no reutiliza su código, solo lee el catálogo de la base (sc_campos,
sc_modulos) y las listas de config/. Revisa los GeoPackage públicos o el esquema «publico» de PostGIS.

Para todo objeto falla (código 1) si encuentra:
  · una tabla con el nombre de una capa excluida,
  · una columna con el prefijo de un módulo interno (cyc_, hmz_, lpm_, cmpa_, cco_, vgd_, cmz_),
  · en PostGIS, una tabla sin registro en publico.procedencia.
Para los objetos tal cual (marcos y capas), además:
  · una columna cuyo nombre solo existe como «interno» en el catálogo,
  · una columna que coincide con los patrones de las reglas vigentes (homicidio, víctimas, comparendos, kde).
Para los productos transformados (etapa 35), en lugar de esos dos chequeos de nombre, que tratarían como fuga
un nombre legítimo como «tasa_homicidio», se revisan los valores:
  · todo conteo (columna «n» o «n_*») tiene que ser vacío, 0 o al menos K,
  · ninguna columna puede ser un atributo individual (fecha, hora, edad, sexo, arma, …).
K es el mayor entre el k de transformaciones.conf y el piso de la regla vigente (5 víctimas): bajar k en la
configuración no relaja la auditoría.
Para lo que viene de la base canónica (etapa 25: Parquet filtrado, COG, recortes y tablas can_*), la referencia no es
el catálogo de la base de referencia sino los de la canónica que están en la zona interna: falla si un archivo o una
tabla viene de una ruta prohibida o de un archivo que el inventario no marca «abierta/pública», o si una columna no
está en el catálogo de su archivo con sensibilidad pública.
Uso:
  60_auditar_divulgacion.py --base BASE --conf CONFIG --gpkg A.gpkg ... [--transformados T.gpkg] [--canonica DIR]
  60_auditar_divulgacion.py --base BASE --conf CONFIG --postgis sitecali
"""
import argparse
import configparser
import os
import re
import sqlite3
import subprocess
import sys

K_PISO = 5   # regla vigente de la base: divulgación con 5 víctimas
PATRONES_REGLAS = [r'^hom_', r'^hmz_', r'^cmp_', r'^cmpa_', r'^kde_', r'^cc_v_', r'^cc_ef_mz', r'^eventos_(?!por_anio$)',
                   r'homicid', r'v[ií]ctima', r'comparend']
# Atributos individuales, con límites de palabra para no confundir «precariedad» con «edad» ni «spearman» con «arma».
ATRIBUTOS_INDIVIDUALES = re.compile(
    r'(^|_)(fecha|hora|hora_n|edad|grupo_edad|menor_edad|sexo|anio_mes|mes|dia|arma|arma_fuego|nombre_victima|'
    r'documento|direccion|telefono)(_|$)', re.I)
TABLAS_SISTEMA = {'procedencia', 'diccionario', 'gpkg_contents', 'gpkg_geometry_columns',
                  'gpkg_spatial_ref_sys', 'gpkg_tile_matrix', 'gpkg_tile_matrix_set', 'gpkg_extensions',
                  'gpkg_ogr_contents', 'sqlite_sequence', 'layer_styles'}
COLUMNAS_SISTEMA = {'fid', 'geom', 'geometry', 'vigencia'}
COLUMNAS_GEOMETRIA = {'fid', 'geom', 'geometry'}
ES_CONTEO = re.compile(r'^n(_.+)?$')


def reglas(base, conf_dir):
    con = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    prefijos = sorted(p + '_' for (p,) in con.execute("SELECT prefijo FROM sc_modulos WHERE divulgacion = 'interno'"))
    por_nombre = {}
    for campo, div in con.execute('SELECT campo, divulgacion FROM sc_campos'):
        por_nombre.setdefault(campo.lower(), set()).add(div)
    solo_internos = {c for c, d in por_nombre.items() if d == {'interno'}}
    excluidas = set()
    with open(os.path.join(conf_dir, 'capas_excluidas.conf'), encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith('#'):
                excluidas.add(linea.split('|')[0].strip().lower())
    k = K_PISO
    ruta_t = os.path.join(conf_dir, 'transformaciones.conf')
    if os.path.exists(ruta_t):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(ruta_t, encoding='utf-8')
        k = max(K_PISO, cp.getint('general', 'k', fallback=K_PISO))
    return prefijos, solo_internos, excluidas, k


class FuenteGpkg:
    def __init__(self, rutas, transformados):
        self.objetos = {}   # nombre visible -> (ruta, tabla, transformado)
        for ruta, es_t in [(r, False) for r in rutas] + ([(transformados, True)] if transformados else []):
            con = sqlite3.connect(f'file:{ruta}?mode=ro', uri=True)
            for (t,) in con.execute('SELECT table_name FROM gpkg_contents'):
                self.objetos[f'{os.path.basename(ruta)}:{t}'] = (ruta, t, es_t)
        self.registrados = None

    def columnas(self, obj):
        ruta, t, _ = self.objetos[obj]
        return [r[1] for r in sqlite3.connect(f'file:{ruta}?mode=ro', uri=True).execute(f'PRAGMA table_info("{t}")')]

    def transformado(self, obj):
        return self.objetos[obj][2]

    def violaciones_k(self, obj, col, k):
        ruta, t, _ = self.objetos[obj]
        con = sqlite3.connect(f'file:{ruta}?mode=ro', uri=True)
        return con.execute(f'SELECT count(*) FROM "{t}" WHERE "{col}" > 0 AND "{col}" < ?', (k,)).fetchone()[0]


class FuentePostgis:
    def __init__(self, db):
        self.db = db
        self.cols = {}
        for t, c in self.q("SELECT table_name, column_name FROM information_schema.columns "
                           "WHERE table_schema = 'publico' ORDER BY table_name, ordinal_position"):
            self.cols.setdefault(t, []).append(c)
        self.objetos = self.cols
        self.registrados, self.tipos = set(), {}
        if 'procedencia' in self.cols:
            for o, tp in self.q('SELECT objeto, tipo FROM publico.procedencia'):
                self.registrados.add(o)
                self.tipos[o] = tp

    def q(self, sql):
        r = subprocess.run(['psql', '-X', '-qAt', '-F', '\t', '-d', self.db, '-c', sql],
                           capture_output=True, text=True, check=True)
        return [l.split('\t') for l in r.stdout.splitlines() if l]

    def columnas(self, obj):
        return self.cols[obj]

    def transformado(self, obj):
        return self.tipos.get(obj) == 'transformado'

    def violaciones_k(self, obj, col, k):
        return int(self.q(f'SELECT count(*) FROM publico."{obj}" WHERE "{col}" > 0 AND "{col}" < {int(k)}')[0][0])


def auditar(fuente, prefijos, solo_internos, excluidas, k):
    fallas = []
    for obj in fuente.objetos:
        tabla = obj.split(':')[-1].lower()
        if tabla in TABLAS_SISTEMA:
            continue
        if tabla in excluidas:
            fallas.append(f'{obj}: es una capa excluida')
        if fuente.registrados is not None and tabla not in fuente.registrados:
            fallas.append(f'{obj}: no tiene registro en publico.procedencia')
        transformado = fuente.transformado(obj)
        for c in fuente.columnas(obj):
            cl = c.lower()
            if cl in COLUMNAS_SISTEMA:
                continue
            p = next((p for p in prefijos if cl.startswith(p)), None)
            if p:
                fallas.append(f'{obj}.{c}: prefijo de módulo interno «{p}»')
                continue
            if transformado:
                if ATRIBUTOS_INDIVIDUALES.search(cl):
                    fallas.append(f'{obj}.{c}: atributo individual en un producto transformado')
                elif ES_CONTEO.match(cl):
                    malos = fuente.violaciones_k(obj, c, k)
                    if malos:
                        fallas.append(f'{obj}.{c}: {malos} valores entre 1 y {k - 1} (regla de {k} eventos)')
            elif cl in solo_internos:
                fallas.append(f'{obj}.{c}: el catálogo solo conoce este campo como interno')
            elif any(re.search(r, cl) for r in PATRONES_REGLAS):
                fallas.append(f'{obj}.{c}: coincide con un patrón de las reglas de divulgación')
    return fallas


class ReglasCanonica:
    """Catálogo, inventario y patrones prohibidos de la canónica, leídos de la zona interna y de config/canonica.conf."""

    def __init__(self, raiz, conf_dir):
        import csv
        import fnmatch
        import glob
        self.fnmatch = fnmatch.fnmatch
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(os.path.join(conf_dir, 'canonica.conf'), encoding='utf-8')
        sp = lambda v: [x.strip() for x in re.split(r'[,\n]', v) if x.strip()]
        self.prohibidos = sp(cp['paquete']['prohibidos'])
        self.sens = set(sp(cp['compuerta']['sensibilidades_publicas']))
        self.cond = set(sp(cp['compuerta']['condiciones_publicas']))
        self.cat, self.inv = {}, {}
        for f in glob.glob(os.path.join(raiz, 'catalogo_canonica_bloque*.csv')):
            with open(f, encoding='utf-8') as fh:
                for r in csv.DictReader(fh):
                    self.cat.setdefault(r['archivo'], {})[r['columna'].lower()] = r['sensibilidad'].strip()
        for f in glob.glob(os.path.join(raiz, 'inventario_canonica*.csv')):
            with open(f, encoding='utf-8') as fh:
                for r in csv.DictReader(fh):
                    self.inv[r['archivo']] = r['condicion'].strip()
        if not self.cat or not self.inv:
            sys.exit(f'no hay catálogos o inventario de la canónica en {raiz}')
        self.por_tabla = {}
        for ruta in set(self.cat) | set(self.inv):
            nombre = 'can_' + re.sub(r'[^a-z0-9_]', '_', os.path.splitext(ruta)[0].lower().replace('/', '_'))
            self.por_tabla[nombre] = ruta

    def archivo(self, ruta, columnas, etiqueta):
        fallas = []
        if any(self.fnmatch(ruta.lower(), p.lower()) for p in self.prohibidos):
            return [f'{etiqueta}: ruta prohibida de la canónica ({ruta})']
        if self.inv.get(ruta) not in self.cond:
            fallas.append(f'{etiqueta}: el inventario no marca {ruta} como público ({self.inv.get(ruta, "no figura")})')
        cat = self.cat.get(ruta, {})
        for c in columnas:
            cl = c.lower()
            if cl in COLUMNAS_GEOMETRIA:
                continue
            s = cat.get(cl)
            if s is None:
                fallas.append(f'{etiqueta}.{c}: columna sin catálogo en la canónica')
            elif s not in self.sens:
                fallas.append(f'{etiqueta}.{c}: sensibilidad «{s}» en el catálogo de la canónica')
        return fallas


def auditar_canonica_dir(directorio, reglas):
    import pyarrow.parquet as pq
    fallas, n = [], 0
    base_pq = os.path.join(directorio, 'parquet')
    for d, _, fs in os.walk(base_pq):
        for f in fs:
            ruta = os.path.relpath(os.path.join(d, f), base_pq).replace(os.sep, '/')
            n += 1
            fallas += reglas.archivo(ruta, pq.read_schema(os.path.join(d, f)).names, f'parquet:{ruta}')
    for sub in ('cog', 'segmentos'):
        for d, _, fs in os.walk(os.path.join(directorio, sub)):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), os.path.join(directorio, sub)).replace(os.sep, '/')
                n += 1
                if sub == 'cog':
                    ruta = rel
                else:  # segmentos/<ráster>/<unidad>.tif: el origen es el ráster con ese nombre en el inventario
                    origen = rel.split('/')[0] + '.tif'
                    ruta = next((r for r in reglas.inv if r.endswith('/' + origen)), origen)
                fallas += reglas.archivo(ruta, [], f'{sub}:{rel}')
    gpkg = os.path.join(directorio, 'canonica_publica.gpkg')
    if os.path.exists(gpkg):
        con = sqlite3.connect(f'file:{gpkg}?mode=ro', uri=True)
        for (t,) in con.execute('SELECT table_name FROM gpkg_contents'):
            n += 1
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            ruta = reglas.por_tabla.get(t.lower())
            fallas += ([f'canonica_publica.gpkg:{t}: no corresponde a ningún archivo de la canónica'] if ruta is None
                       else reglas.archivo(ruta, cols, f'canonica_publica.gpkg:{t}'))
    return fallas, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', required=True)
    ap.add_argument('--conf', required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--gpkg', nargs='+')
    g.add_argument('--postgis')
    ap.add_argument('--transformados')
    ap.add_argument('--canonica', help='carpeta publico/canonica de la etapa 25 (Parquet, COG, recortes, gpkg)')
    ap.add_argument('--catalogo-canonica', help='canónica en la zona interna (por defecto, junto a la base)')
    a = ap.parse_args()

    prefijos, solo_internos, excluidas, k = reglas(a.base, a.conf)
    fuente = FuenteGpkg(a.gpkg, a.transformados) if a.gpkg else FuentePostgis(a.postgis)
    raiz_c = a.catalogo_canonica or os.path.join(os.path.dirname(os.path.abspath(a.base)), 'canonica')
    reglas_c = ReglasCanonica(raiz_c, a.conf) if (a.canonica or a.postgis) and os.path.isdir(raiz_c) else None
    if a.postgis:   # las tablas can_* se auditan contra la canónica, no contra el catálogo de la base de referencia
        canonicas = {o for o in fuente.objetos if o.lower().startswith('can_')}
        if canonicas and reglas_c is None:
            sys.exit(f'hay tablas can_* en PostGIS pero no está la canónica en {raiz_c} para auditarlas')
        for o in canonicas:
            fuente.objetos = {k: v for k, v in fuente.objetos.items() if k != o} \
                if isinstance(fuente.objetos, dict) else fuente.objetos
    fallas = auditar(fuente, prefijos, solo_internos, excluidas, k)
    n_can = 0
    if a.postgis and reglas_c is not None:
        for o in sorted(canonicas):
            n_can += 1
            if o not in fuente.registrados:
                fallas.append(f'{o}: no tiene registro en publico.procedencia')
            ruta = reglas_c.por_tabla.get(o.lower())
            fallas += ([f'{o}: no corresponde a ningún archivo de la canónica'] if ruta is None
                       else reglas_c.archivo(ruta, fuente.cols[o], o))
    if a.canonica:
        f_c, n_can = auditar_canonica_dir(a.canonica, reglas_c) if reglas_c else ([f'falta la canónica en {raiz_c}'], 0)
        fallas += f_c
    destino = 'PostGIS ' + a.postgis if a.postgis else f'{len(a.gpkg) + bool(a.transformados)} GeoPackage'
    n_t = sum(1 for o in fuente.objetos if fuente.transformado(o))
    n_cols = sum(len(fuente.columnas(o)) for o in fuente.objetos)
    if fallas:
        print(f'AUDITORÍA FALLIDA en {destino}: {len(fallas)} hallazgos', file=sys.stderr)
        for f in fallas[:40]:
            print('  ' + f, file=sys.stderr)
        sys.exit(1)
    print(f'auditoría sin hallazgos en {destino}: {len(fuente.objetos)} tablas ({n_t} transformadas, k = {k}), '
          f'{n_cols} columnas revisadas contra {len(prefijos)} prefijos internos, {len(solo_internos)} nombres solo '
          f'internos y {len(excluidas)} capas excluidas'
          + (f'; {n_can} objetos de la canónica contra su catálogo' if n_can else ''))


if __name__ == '__main__':
    main()
