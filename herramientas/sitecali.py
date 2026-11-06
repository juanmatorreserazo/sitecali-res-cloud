#!/usr/bin/env python3
# sitecali.py · Mecanismo de crecimiento de la base integrada SITECALI-Res (desde la v2.4.0)
#
# Principios
#   1. Lo que ya está en la base no se toca. Al inicializar, cada capa y tabla queda registrada con su huella SHA-256
#      y `validar` comprueba que no haya cambiado. La tabla `manzanas` (1.023 atributos) no recibe más columnas.
#   2. Lo nuevo entra como módulos: tablas `m_<modulo>` unidas al territorio por una llave canónica (manzana, sección,
#      sector, barrio, comuna, unidad administrativa, UPU o ciudad). El año va en filas (`vigencia`), no en columnas
#      ni en tablas por año. Un módulo cargado también queda congelado: para corregirlo se carga una versión nueva.
#   3. Cada campo queda catalogado con su naturaleza (observado, estimación oficial, derivado, modelo) y su
#      divulgación (público, condicionado, interno). `marco --publico` se niega a exportar lo interno.
#   4. Cada operación sube la versión y queda en la bitácora `sc_version`.
#
# Uso (ver LEEME_mecanismo.md):
#   python3 sitecali.py inicializar base_integrada_cali_v234.gpkg base_integrada_cali_v24.gpkg
#   python3 sitecali.py agregar BASE archivo --modulo NOMBRE --prefijo PRE --nivel comuna --llave COL --fuente "..." ...
#   python3 sitecali.py puntos-a-manzana BASE --tabla CAPA_PUNTOS --modulo NOMBRE --prefijo PRE [--anio-de COL]
#   python3 sitecali.py clasificar BASE --tabla T [--campo C | --patron REGEX] [--naturaleza N] [--divulgacion D]
#   python3 sitecali.py validar BASE [--rapido]
#   python3 sitecali.py catalogo BASE [--buscar TEXTO] [--exportar archivo.csv]
#   python3 sitecali.py marco BASE --campos a,b,tabla.c,pre_* --nivel manzana [--vigencia 2024] [--publico] -o salida
#   python3 sitecali.py empaquetar BASE carpeta_salida
import argparse, datetime as dt, fnmatch, hashlib, json, os, re, shutil, sqlite3, sys, unicodedata, zipfile
import numpy as np
import pandas as pd

SRS = 9377
MPIO = '76001'
NIVELES = {  # nivel -> columna en sc_llaves
    'manzana': 'COD_DANE_A', 'seccion': 'seccion', 'sector': 'sector', 'barrio': 'id_barrio',
    'unidad_barrial': 'barrio_u', 'comuna': 'comuna_n',
    'unidad_admin': 'unidad_admin', 'upu': 'upu', 'ciudad': 'mpio'}
NIVELES_SIN_LLAVE = ('punto', 'registro', 'otro')
NATURALEZAS = ('llave', 'tecnico', 'geometria', 'observado', 'estimacion_oficial', 'derivado', 'modelo', 'sin_clasificar')
DIVULGACION = ('publico', 'condicionado', 'interno')
SISTEMA = ('sc_version', 'sc_modulos', 'sc_tablas', 'sc_campos', 'sc_llaves')
NO_CONGELAR = ('diccionario',) + SISTEMA   # documentación viva
MAX_COLUMNAS = 1800                        # SQLite admite 2.000 por defecto; PostGIS, 1.600

# --- reglas para clasificar lo heredado de la v2.3.4 -------------------------------------------------------------
TABLAS_INTERNAS = {'homicidios', 'cc_efectos_manzana', 'cc_secciones', 'cc_concentracion_intrabarrial',
                   'cc_lisa_conglomerados_mz'}
CAMPOS_INTERNOS = [r'^cc_v_', r'^cc_trayectoria', r'^cc_ef_mz', r'^cc_ef_resid', r'^cc_lisa_mz$',
                   r'^cc_tipo_seccion_propio$', r'^hom_', r'^cmp_', r'^kde_', r'^eventos_']
TEXTO_SENSIBLE = r'homicid|v[ií]ctima|comparendo|feminicid|lesionad'
CAMPOS_MODELO = [r'^cc_ef_', r'^cc_rr', r'^cc_pv', r'^cc_trayectoria', r'^cc_lisa', r'^v2_', r'^pro_', r'^pred_',
                 r'^z30_', r'^cc_tipo', r'^cc_bivar', r'^cc_nucleo', r'bym', r'^kde_']
TEXTO_MODELO = r'modelo|posterior|probabilidad|predic|bayes|suavizad|conglomerado lisa'
CONDICIONADOS = r'^(v|v\d.*|v_.*|vh.*|vmujer|tasa.*|rr.*|k_rr.*|k_pv.*|pct_v.*|micro.*|macro|mz_alta|pob_mz_alta|cmp.*|casos)$'
TABLAS_POR_FAMILIA = [  # (patrón del nombre de la tabla, naturaleza) para lo heredado sin diccionario; gana el primero
    (r'^cc_(particion|explicativos|bym2|ml_|shap|espacial|sdm|bivariados|trayectorias|techo|robustez|moran|'
     r'correlograma|lisa|efectos|excepciones|micro_calientes|diag_)', 'modelo'),
    (r'^pro_|^det_(percolacion|difusion|validacion)|^nc_(env|inq)2031|^ce_receptores_2031', 'modelo'),
    (r'^proy_(cali_edad_sexo|edades_escolares)', 'estimacion_oficial'),
    (r'^cc_|^(nc|det|ce|af|pmi|ahdi|vacios|crecimiento|tasas|archivo|lea|islas|jarillon)_|^secciones_deterioro|'
     r'^barrios_corona|^pot_(area|tipo)_|^elec_(ciudad|comunas)|^vi_|^pob_edad_sexo|^proy_comuna_pob_viv|'
     r'^edu_matricula_2025_comuna|^geih_cali_cambio', 'derivado'),
    (r'^(pot_|idesc_|pgirs_|amb_|dep_|nomenclatura|salud_|sedes_|edu_|elec_|reps_|bomberos_|crue_|visitas_|vial_|'
     r'vic_ruv|geih_|homicidios|establecimientos_)|^(barrios|veredas|comunas_corregimientos)$', 'observado')]
TABLAS_INTERNAS_PATRON = r'homicid|^visitas_riesgo'
LLAVES_HEREDADAS = {  # tabla: (nivel, columna)
    'cc_secciones': ('seccion', 'seccion'), 'secciones_deterioro': ('seccion', 'secu'),
    'cc_sectores': ('sector', 'sector'), 'cc_barrios': ('unidad_barrial', 'barrio_id'), 'barrios': ('barrio', 'ID_BARRIO'),
    'cc_comunas': ('comuna', 'comuna'), 'comunas_corregimientos': ('unidad_admin', 'unidad_admin'),
    'cc_upu': ('upu', 'upu'), 'cc_upu_atlas': ('upu', 'upu')}


# ================================================================================================ utilidades
def ahora():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def norm_txt(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', s).strip()


def slug(s):
    return re.sub(r'_+', '_', re.sub(r'[^a-z0-9]+', '_', norm_txt(s))).strip('_') or 'campo'


def sha256_archivo(ruta, bloque=1 << 20):
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for b in iter(lambda: f.read(bloque), b''):
            h.update(b)
    return h.hexdigest()


def huella_tabla(con, tabla):
    """SHA-256 del contenido (nombres de columna y todas las filas en orden de rowid)."""
    h = hashlib.sha256()
    es_vista = con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (tabla,)).fetchone()
    cur = con.execute(f'SELECT * FROM "{tabla}" ORDER BY {"fid" if es_vista else "rowid"}')
    h.update(repr([d[0] for d in cur.description]).encode())
    while True:
        filas = cur.fetchmany(5000)
        if not filas:
            break
        for f in filas:
            h.update(repr(f).encode())
    return h.hexdigest()


def columnas(con, tabla):
    return [(r[1], (r[2] or '').upper()) for r in con.execute(f'PRAGMA table_info("{tabla}")')]


def tablas_gpkg(con):
    return [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents ORDER BY table_name")]


def geom_info(con):
    return {r[0]: (r[1], r[2], r[3]) for r in
            con.execute('SELECT table_name, column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns')}


def version_actual(con):
    r = con.execute('SELECT version FROM sc_version ORDER BY fid DESC LIMIT 1').fetchone()
    return r[0] if r else None


def subir_version(con, operacion, detalle, nueva=None):
    v = version_actual(con)
    if nueva is None:
        a, b, c = (int(x) for x in v.split('.'))
        nueva = f'{a}.{b}.{c + 1}'
    n_t = con.execute("SELECT count(*) FROM gpkg_contents").fetchone()[0]
    n_c = con.execute("SELECT count(*) FROM sc_campos").fetchone()[0]
    con.execute('INSERT INTO sc_version (version, fecha, operacion, detalle, n_tablas, n_campos) VALUES (?,?,?,?,?,?)',
                (nueva, ahora(), operacion, json.dumps(detalle, ensure_ascii=False, default=str), n_t, n_c))
    return nueva


def registrar_atributos(con, tabla, descripcion=''):
    con.execute("INSERT OR REPLACE INTO gpkg_contents (table_name, data_type, identifier, description, last_change) "
                "VALUES (?, 'attributes', ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))", (tabla, tabla, descripcion))


def exigir_inicializada(con):
    falta = [t for t in SISTEMA if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()]
    if falta:
        sys.exit(f'La base no está inicializada (faltan {", ".join(falta)}). Corra primero: sitecali.py inicializar')


def a_sql(v):
    if v is None or (not isinstance(v, (str, bytes)) and pd.isna(v)):
        return None
    if isinstance(v, (pd.Timestamp, dt.date, dt.datetime)):
        return v.isoformat()
    return v.item() if hasattr(v, 'item') else v


def tipo_sql(serie):
    if pd.api.types.is_bool_dtype(serie) or pd.api.types.is_integer_dtype(serie):
        return 'INTEGER'
    if pd.api.types.is_float_dtype(serie):
        return 'REAL'
    return 'TEXT'


def normalizar_llave(serie, nivel, dominio=None):
    """Lleva una llave a su forma canónica. `dominio` (valores canónicos de sc_llaves) se usa en llaves de texto."""
    s = serie.copy()
    if nivel in ('manzana', 'seccion', 'sector'):
        largo = {'manzana': 22, 'seccion': 20, 'sector': 18}[nivel]
        s = s.astype('string').str.strip().str.replace(r'\.0$', '', regex=True).str.zfill(largo)
        return s.where(serie.notna())
    if nivel == 'comuna':
        return pd.to_numeric(s.astype('string').str.extract(r'(\d+)')[0], errors='coerce').astype('Int64')
    if nivel == 'barrio':
        s = s.astype('string').str.strip().str.replace(r'^[Bb]', '', regex=True).str.replace(r'\.0$', '', regex=True)
        return s.str.zfill(4).where(serie.notna())
    if nivel == 'unidad_barrial':   # B + barrio, V + vereda o C + comuna (regla del script 301)
        s = s.astype('string').str.strip().str.upper().str.replace(r'\.0$', '', regex=True)
        s = s.where(~s.str.fullmatch(r'\d{1,4}', na=False), 'B' + s.str.zfill(4))
        s = s.where(~s.str.fullmatch(r'C\d', na=False), 'C0' + s.str[1:])
        return s.where(serie.notna())
    if nivel == 'ciudad':
        return pd.Series(MPIO, index=s.index, dtype='string')
    # texto: upu, unidad_admin
    if dominio is not None:
        def k(x):
            return re.sub(r'^(el|la|los|las) ', '', norm_txt(x))
        mapa = {}
        for v in dominio:
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                mapa.setdefault(norm_txt(v), v)
                mapa.setdefault(k(v), v)
        return s.map(lambda x: mapa.get(norm_txt(x), mapa.get(k(x), x)) if pd.notna(x) else x).astype('string')
    return s.astype('string')


def dominio_llave(con, nivel):
    col = NIVELES[nivel]
    vals = [r[0] for r in con.execute(f'SELECT DISTINCT "{col}" FROM sc_llaves WHERE "{col}" IS NOT NULL')]
    return pd.Series(vals)


# ================================================================================================ inicializar
DDL = """
CREATE TABLE sc_version (fid INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL, fecha TEXT, operacion TEXT,
    detalle TEXT, n_tablas INTEGER, n_campos INTEGER);
CREATE TABLE sc_modulos (fid INTEGER PRIMARY KEY AUTOINCREMENT, modulo TEXT UNIQUE NOT NULL, prefijo TEXT UNIQUE NOT NULL,
    tabla TEXT NOT NULL, nivel TEXT, llave TEXT, temporal INTEGER, vigencia_min INTEGER, vigencia_max INTEGER,
    fuente TEXT, naturaleza TEXT, divulgacion TEXT, descripcion TEXT, archivo_origen TEXT, sha256_origen TEXT,
    filas INTEGER, columnas INTEGER, cobertura REAL, version_entrada TEXT, fecha TEXT, estado TEXT, reemplaza TEXT);
CREATE TABLE sc_tablas (fid INTEGER PRIMARY KEY AUTOINCREMENT, tabla TEXT UNIQUE NOT NULL, modulo TEXT, nivel TEXT,
    llave TEXT, tipo TEXT, geometria TEXT, filas INTEGER, columnas INTEGER, sha256 TEXT, congelada INTEGER,
    version_entrada TEXT);
CREATE TABLE sc_campos (fid INTEGER PRIMARY KEY AUTOINCREMENT, tabla TEXT NOT NULL, campo TEXT NOT NULL, tipo_sql TEXT,
    modulo TEXT, naturaleza TEXT, divulgacion TEXT, origen TEXT, version_entrada TEXT, descripcion TEXT,
    documentado INTEGER, UNIQUE (tabla, campo));
CREATE TABLE sc_llaves (fid INTEGER PRIMARY KEY AUTOINCREMENT, COD_DANE_A TEXT UNIQUE NOT NULL, seccion TEXT, sector TEXT,
    zona TEXT, comuna_n INTEGER, comuna TEXT, unidad_admin TEXT, id_barrio TEXT, barrio TEXT, id_vereda TEXT,
    corregimiento TEXT, barrio_u TEXT, upu TEXT, mpio TEXT);
CREATE INDEX sc_llaves_barrio_u ON sc_llaves (barrio_u);
CREATE INDEX sc_llaves_seccion ON sc_llaves (seccion);
CREATE INDEX sc_llaves_sector ON sc_llaves (sector);
CREATE INDEX sc_llaves_comuna ON sc_llaves (comuna_n);
CREATE INDEX sc_llaves_barrio ON sc_llaves (id_barrio);
CREATE INDEX sc_campos_campo ON sc_campos (campo);
"""
DESC_SISTEMA = {
    'sc_version': 'Bitácora de versiones del mecanismo de crecimiento (sitecali.py)',
    'sc_modulos': 'Registro de módulos cargados después de la v2.3.4',
    'sc_tablas': 'Registro de todas las capas y tablas con nivel, llave, huella SHA-256 y congelamiento',
    'sc_campos': 'Catálogo de todos los campos: naturaleza, divulgación, origen, versión y descripción',
    'sc_llaves': 'Llaves canónicas de cada manzana hacia sección, sector, barrio, comuna, unidad administrativa, UPU y ciudad'}


def clasificar_heredado(tabla, campo, tsql, origen, desc, geom_col, tiene_publicable):
    texto = f'{desc or ""}'
    if campo == geom_col:
        nat = 'geometria'
    elif campo == 'fid':
        nat = 'tecnico'
    elif campo in ('COD_DANE_A', 'seccion', 'sector', 'barrio_id', 'ID_BARRIO', 'comuna', 'upu', 'unidad_admin',
                   'secu', 'vigencia') and tabla != 'manzanas' or campo == 'COD_DANE_A':
        nat = 'llave'
    elif any(re.search(p, campo) for p in CAMPOS_MODELO) or re.search(TEXTO_MODELO, texto, re.I):
        nat = 'modelo'
    elif origen and re.search(r'deriv|script|v\d+\.\d', origen, re.I):
        nat = 'derivado'
    elif origen:
        nat = 'observado'
    else:
        nat = next((n for p, n in TABLAS_POR_FAMILIA if re.search(p, tabla)), 'sin_clasificar')
    sensible = (re.search(TEXTO_SENSIBLE, texto, re.I) or re.search(r'homicid|victim|cmp_|comparend', campo, re.I)
                or (tiene_publicable and re.fullmatch(CONDICIONADOS, campo)))
    if (tabla in TABLAS_INTERNAS or re.search(TABLAS_INTERNAS_PATRON, tabla)
            or any(re.search(p, campo) for p in CAMPOS_INTERNOS)):
        div = 'interno'
    elif sensible:
        div = 'condicionado' if tiene_publicable else 'interno'
    else:
        div = 'publico'
    if nat in ('geometria', 'tecnico', 'llave') and tabla not in TABLAS_INTERNAS:
        div = 'publico'
    m = re.search(r'v(\d+(?:\.\d+){1,2})', origen or '')
    ver = f'v{m.group(1)}' if m else '≤v2.3.4'
    return nat, div, ver


def unidad_barrial(m):
    """Unidad barrial de la serie 300 (script 301): B + barrio oficial; si no hay, el barrio más cercano a 400 m o
    menos (buscado por nombre dentro de la comuna); si no, V + vereda; si no, C + comuna. Solo la cabecera de las
    comunas 1 a 22; en los centros poblados, V + vereda."""
    u = (m.zona == 'Cabecera') & pd.to_numeric(m.comuna_n, errors='coerce').between(1, 22)
    name2id = (m[u].dropna(subset=['id_barrio']).groupby(['comuna_n', 'barrio'])['id_barrio']
               .agg(lambda s: s.mode().iat[0]))
    out = []
    for r, en_u in zip(m.itertuples(index=False), u):
        if not en_u:
            out.append('V' + str(r.id_vereda) if pd.notna(r.id_vereda) else None)
        elif pd.notna(r.id_barrio):
            out.append('B' + str(r.id_barrio))
        elif (pd.notna(r.barrio_cercano) and (r.dist_barrio_cercano_m or 9e9) <= 400
              and (r.comuna_n, r.barrio_cercano) in name2id.index):
            out.append('B' + str(name2id[(r.comuna_n, r.barrio_cercano)]))
        elif pd.notna(r.id_vereda):
            out.append('V' + str(r.id_vereda))
        else:
            out.append('C%02d' % int(r.comuna_n))
    return out


def cmd_inicializar(a):
    if os.path.exists(a.salida) and not a.sobrescribir:
        sys.exit(f'{a.salida} ya existe (use --sobrescribir)')
    con0 = sqlite3.connect(a.base)
    if con0.execute("SELECT 1 FROM sqlite_master WHERE name='sc_version'").fetchone():
        sys.exit('La base de entrada ya está inicializada')
    cols_mz = [c for c, _ in columnas(con0, 'manzanas')]
    if 'cc_universo' not in cols_mz or 'v2_inq_alto_2018' not in cols_mz:
        print('AVISO: la base no parece la v2.3.4 completa (faltan campos cc_ o v2_).')
    con0.close()
    shutil.copyfile(a.base, a.salida)
    con = sqlite3.connect(a.salida)
    t0 = dt.datetime.now()
    try:
        con.executescript(DDL)
        for t, d in DESC_SISTEMA.items():
            registrar_atributos(con, t, d)
        # --- llaves canónicas
        m = pd.read_sql('SELECT COD_DANE_A, zona, comuna, comuna_n, id_barrio, barrio, id_vereda, corregimiento, '
                        'pot_upu, barrio_cercano, dist_barrio_cercano_m FROM manzanas ORDER BY fid', con)
        ll = pd.DataFrame({'COD_DANE_A': m.COD_DANE_A.astype(str)})
        ll['seccion'] = ll.COD_DANE_A.str[:20]
        ll['sector'] = ll.COD_DANE_A.str[:18]
        ll['zona'] = m.zona
        ll['comuna_n'] = pd.to_numeric(m.comuna_n, errors='coerce').astype('Int64')
        ll['comuna'] = m.comuna
        urb = m.comuna.fillna('').str.startswith('Comuna')
        ll['unidad_admin'] = np.where(urb, m.comuna, m.corregimiento)
        ll['unidad_admin'] = ll.unidad_admin.replace('', None)
        ll['id_barrio'] = normalizar_llave(m.id_barrio, 'barrio')
        ll['barrio'] = m.barrio
        ll['id_vereda'] = m.id_vereda
        ll['corregimiento'] = m.corregimiento
        ll['barrio_u'] = unidad_barrial(m)
        ll['upu'] = m.pot_upu
        ll['mpio'] = MPIO
        con.executemany('INSERT INTO sc_llaves (COD_DANE_A, seccion, sector, zona, comuna_n, comuna, unidad_admin, '
                        'id_barrio, barrio, id_vereda, corregimiento, barrio_u, upu, mpio) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        [tuple(None if pd.isna(v) else (int(v) if isinstance(v, (np.integer,)) else v) for v in r)
                         for r in ll.itertuples(index=False)])
        # --- registro y huella de lo heredado
        dic = pd.read_sql('SELECT capa, campo, origen, descripcion FROM diccionario', con)
        dic = dic.drop_duplicates(['capa', 'campo'], keep='last').set_index(['capa', 'campo'])
        gi = geom_info(con)
        n_campos = 0
        for t in tablas_gpkg(con):
            if t in SISTEMA:
                continue
            cols = columnas(con, t)
            nombres = [c for c, _ in cols]
            tipo = 'features' if t in gi else 'attributes'
            filas = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            if t in LLAVES_HEREDADAS:
                nivel, llave = LLAVES_HEREDADAS[t]
            elif 'COD_DANE_A' in nombres:
                u = con.execute(f'SELECT count(DISTINCT COD_DANE_A) FROM "{t}"').fetchone()[0]
                nivel, llave = ('manzana' if u == filas else ('punto' if tipo == 'features' else 'registro')), 'COD_DANE_A'
            elif tipo == 'features' and gi[t][1] in ('POINT', 'MULTIPOINT'):
                nivel, llave = 'punto', None
            else:
                nivel, llave = 'otro', None
            congelada = 0 if t in NO_CONGELAR else 1
            sha = huella_tabla(con, t) if congelada else None
            con.execute('INSERT INTO sc_tablas (tabla, modulo, nivel, llave, tipo, geometria, filas, columnas, sha256, '
                        'congelada, version_entrada) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (t, 'legado_v2.3.4', nivel, llave, tipo, gi[t][1] if t in gi else None, filas, len(cols), sha,
                         congelada, '≤v2.3.4'))
            geom_col = gi[t][0] if t in gi else None
            tiene_pub = 'publicable' in nombres
            filas_c = []
            for c, ts in cols:
                origen, desc = (dic.loc[(t, c)].tolist() if (t, c) in dic.index else (None, None))
                nat, div, ver = clasificar_heredado(t, c, ts, origen, desc, geom_col, tiene_pub)
                documentado = int(bool(desc) or nat in ('geometria', 'tecnico'))
                filas_c.append((t, c, ts, 'legado_v2.3.4', nat, div, origen, ver, desc, documentado))
            con.executemany('INSERT INTO sc_campos (tabla, campo, tipo_sql, modulo, naturaleza, divulgacion, origen, '
                            'version_entrada, descripcion, documentado) VALUES (?,?,?,?,?,?,?,?,?,?)', filas_c)
            n_campos += len(filas_c)
        con.execute('INSERT INTO sc_version (version, fecha, operacion, detalle, n_tablas, n_campos) VALUES (?,?,?,?,?,?)',
                    ('2.4.0', ahora(), 'inicializar',
                     json.dumps({'base': os.path.basename(a.base), 'sha256_base': sha256_archivo(a.base),
                                 'nota': 'v2.3.4 congelada; desde aquí crece por módulos'}, ensure_ascii=False),
                     con.execute("SELECT count(*) FROM gpkg_contents").fetchone()[0], n_campos))
        con.commit()
    except Exception:
        con.close()
        os.remove(a.salida)
        raise
    resumen(con)
    print(f'inicializada en {(dt.datetime.now() - t0).seconds} s → {a.salida}')


# ================================================================================================ leer insumos
def leer_insumo(a):
    ext = os.path.splitext(a.archivo.replace('.gz', ''))[1].lower()
    dtype = {a.llave: str} if getattr(a, 'llave', None) else None
    if ext in ('.csv', '.txt', '.tsv'):
        return pd.read_csv(a.archivo, sep=a.sep, engine='python' if a.sep is None else 'c', dtype=dtype,
                           encoding=a.codificacion), None
    if ext in ('.xlsx', '.xls'):
        return pd.read_excel(a.archivo, sheet_name=a.hoja or 0, header=a.encabezado, dtype=dtype), None
    if ext == '.parquet':
        return pd.read_parquet(a.archivo), None
    if ext in ('.gpkg', '.geojson', '.json', '.shp', '.zip', '.fgb'):
        import geopandas as gpd
        g = gpd.read_file(a.archivo, layer=a.capa) if a.capa else gpd.read_file(a.archivo)
        return g, 'geometry'
    sys.exit(f'formato no reconocido: {ext}')


def es_anio(c):
    try:
        v = float(c)
        return v.is_integer() and 1900 <= v <= 2100
    except (TypeError, ValueError):
        return False


# ================================================================================================ agregar
def cargar_modulo(con, base, df, *, modulo, prefijo, nivel, fuente, naturaleza, divulgacion, descripcion,
                  campos_meta=None, archivo_origen=None, cobertura_minima=0.95, descartar_sin_llave=False,
                  forzar=False, reemplaza=None, geometria=None, detalle_extra=None):
    """Valida y carga un DataFrame ya con su llave canónica (columna NIVELES[nivel]) y, si aplica, `vigencia`."""
    exigir_inicializada(con)
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', modulo):
        sys.exit('--modulo: minúsculas, números y _ (3 a 41 caracteres)')
    if not re.fullmatch(r'[a-z][a-z0-9]{1,6}', prefijo):
        sys.exit('--prefijo: 2 a 7 caracteres, minúsculas y números, empezando por letra')
    if naturaleza not in NATURALEZAS[3:7]:
        sys.exit(f'--naturaleza debe ser una de {NATURALEZAS[3:7]}')
    if divulgacion not in DIVULGACION:
        sys.exit(f'--divulgacion debe ser una de {DIVULGACION}')
    if con.execute('SELECT 1 FROM sc_modulos WHERE modulo=?', (modulo,)).fetchone():
        sys.exit(f'ya existe el módulo {modulo}: los módulos no se reescriben; cargue uno nuevo con --reemplaza {modulo}')
    if con.execute('SELECT 1 FROM sc_modulos WHERE prefijo=?', (prefijo,)).fetchone():
        sys.exit(f'el prefijo {prefijo} ya está en uso por otro módulo')
    choque = con.execute("SELECT tabla, campo FROM sc_campos WHERE campo LIKE ? ESCAPE '\\' LIMIT 5",
                         (prefijo.replace('_', '\\_') + '\\_%',)).fetchall()
    if choque:
        sys.exit(f'el prefijo {prefijo}_ choca con campos existentes: {choque}')
    if reemplaza and not con.execute('SELECT 1 FROM sc_modulos WHERE modulo=?', (reemplaza,)).fetchone():
        sys.exit(f'--reemplaza: no existe el módulo {reemplaza}')
    tabla = f'm_{modulo}'
    if con.execute('SELECT 1 FROM sqlite_master WHERE name=?', (tabla,)).fetchone():
        sys.exit(f'ya existe la tabla {tabla}')

    df = df.copy()
    avisos, detalle = [], dict(detalle_extra or {})
    llave = NIVELES.get(nivel)
    cobertura = None
    if llave:
        if llave not in df.columns:
            sys.exit(f'falta la columna de llave {llave}')
        if df[llave].isna().any():
            n = int(df[llave].isna().sum())
            if not descartar_sin_llave:
                sys.exit(f'{n} filas sin llave (use --descartar-sin-llave para dejarlas fuera)')
            df = df[df[llave].notna()]
            detalle['filas_sin_llave_descartadas'] = n
        dom = set(dominio_llave(con, nivel).tolist())
        ok = df[llave].isin(dom)
        cobertura = float(ok.mean()) if len(df) else 0.0
        if not ok.all():
            fuera = df.loc[~ok, llave].astype(str).unique().tolist()
            detalle['llaves_sin_correspondencia'] = fuera[:50]
            if descartar_sin_llave:
                df = df[ok]
                detalle['filas_sin_correspondencia_descartadas'] = int((~ok).sum())
                avisos.append(f'se descartaron {int((~ok).sum())} filas cuya llave no está en el territorio: {fuera[:8]}')
            elif cobertura < cobertura_minima and not forzar:
                sys.exit(f'cobertura de la llave {cobertura:.1%} < {cobertura_minima:.0%}. Sin correspondencia: '
                         f'{fuera[:15]}. Revise la llave o use --descartar-sin-llave / --forzar')
            else:
                avisos.append(f'{int((~ok).sum())} filas con llave fuera del territorio se conservan: {fuera[:8]}')
        claves = [llave] + (['vigencia'] if 'vigencia' in df.columns else [])
        dup = df.duplicated(claves, keep=False)
        if dup.any():
            sys.exit(f'{int(dup.sum())} filas repetidas en ({", ".join(claves)}). Un módulo tiene una fila por unidad '
                     f'(y por vigencia). Ejemplo: {df.loc[dup, claves].head(3).to_dict("records")}')
    elif nivel not in NIVELES_SIN_LLAVE:
        sys.exit(f'--nivel debe ser uno de {list(NIVELES) + list(NIVELES_SIN_LLAVE)}')
    if 'vigencia' in df.columns:
        df['vigencia'] = pd.to_numeric(df['vigencia'], errors='raise').astype('Int64')

    # nombres de campos
    reservados = {llave, 'vigencia', geometria, 'COD_DANE_A', 'asignacion', 'dist_asignacion_m', 'publicable'} - {None}
    renombre, usados = {}, set()
    for c in df.columns:
        if c in reservados:
            continue
        n = slug(c)
        n = n if n.startswith(prefijo + '_') else f'{prefijo}_{n}'
        n = n[:60]
        base_n, k = n, 2
        while n in usados:
            n, k = f'{base_n}_{k}', k + 1
        usados.add(n)
        renombre[c] = n
    meta = {}
    if campos_meta is not None:
        for _, r in campos_meta.iterrows():
            meta[str(r['campo'])] = r
    df = df.rename(columns=renombre)
    for c in renombre.values():
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            num = pd.to_numeric(df[c], errors='coerce')
            if num.notna().sum() == df[c].notna().sum() and df[c].notna().any():
                df[c] = num
    if len(df.columns) > MAX_COLUMNAS:
        sys.exit(f'el módulo tiene {len(df.columns)} columnas: divídalo en módulos temáticos')

    # escribir la tabla
    if geometria:
        import geopandas as gpd
        g = gpd.GeoDataFrame(df, geometry=geometria)
        if g.crs is None:
            sys.exit('la capa no trae sistema de referencia')
        if g.crs.to_epsg() != SRS:
            detalle['crs_origen'] = str(g.crs)
            g = g.to_crs(SRS)
        g = g.rename_geometry('geom')
        g.to_file(base, layer=tabla, driver='GPKG', engine='pyogrio')
        con.execute('UPDATE gpkg_contents SET description=? WHERE table_name=?', (descripcion, tabla))
    else:
        cols_sql = ', '.join(f'"{c}" {tipo_sql(df[c])}' for c in df.columns)
        con.execute(f'CREATE TABLE "{tabla}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, {cols_sql})')
        vals = [tuple(a_sql(v) for v in r) for r in df.itertuples(index=False)]
        con.executemany(f'INSERT INTO "{tabla}" ({", ".join(chr(34) + c + chr(34) for c in df.columns)}) '
                        f'VALUES ({", ".join("?" * len(df.columns))})', vals)
        registrar_atributos(con, tabla, descripcion)
        if llave:
            con.execute(f'CREATE INDEX "{tabla}_llave" ON "{tabla}" ("{llave}"{", vigencia" if "vigencia" in df.columns else ""})')

    try:
        ver = None
        cols = columnas(con, tabla)
        gi = geom_info(con)
        filas_c, sin_desc = [], []
        inverso = {v: k for k, v in renombre.items()}
        for c, ts in cols:
            if c == 'fid':
                nat, div, desc = 'tecnico', 'publico', 'Identificador interno de la fila'
            elif tabla in gi and c == gi[tabla][0]:
                nat, div, desc = 'geometria', divulgacion if nivel == 'punto' else 'publico', f'Geometría (EPSG:{SRS})'
            elif c == llave:
                nat, div, desc = 'llave', 'publico', f'Llave canónica de {nivel} (sc_llaves.{llave})'
            elif c == 'vigencia':
                nat, div, desc = 'llave', 'publico', 'Año al que corresponde el dato'
            elif c in ('COD_DANE_A',) and nivel == 'punto':
                nat, div, desc = 'llave', divulgacion, 'Manzana asignada al punto (sitecali puntos)'
            elif c in ('asignacion', 'dist_asignacion_m'):
                nat, div = 'derivado', divulgacion
                desc = ('Cómo se asignó la manzana: dentro, cercana o sin_asignar' if c == 'asignacion'
                        else 'Distancia en metros a la manzana asignada (0 si cae dentro)')
            else:
                r = meta.get(c, meta.get(inverso.get(c, ''), None))
                nat = (r.get('naturaleza') if r is not None and pd.notna(r.get('naturaleza', None)) else naturaleza)
                div = (r.get('divulgacion') if r is not None and pd.notna(r.get('divulgacion', None)) else divulgacion)
                desc = (r.get('descripcion') if r is not None and pd.notna(r.get('descripcion', None)) else None)
                if nat not in NATURALEZAS or div not in DIVULGACION:
                    raise SystemExit(f'naturaleza o divulgación inválida en {c}')
                if not desc:
                    sin_desc.append(c)
            filas_c.append([tabla, c, ts, modulo, nat, div, fuente, None, desc, int(bool(desc))])
        if sin_desc:
            avisos.append(f'{len(sin_desc)} campos sin descripción (agréguela con clasificar --descripcion): '
                          f'{sin_desc[:8]}')
        vmin = vmax = None
        if 'vigencia' in df.columns:
            vmin, vmax = int(df.vigencia.min()), int(df.vigencia.max())
        detalle.update({'modulo': modulo, 'tabla': tabla, 'filas': len(df), 'columnas': len(cols),
                        'cobertura': cobertura, 'avisos': avisos})
        ver = subir_version(con, 'agregar', detalle)
        for f in filas_c:
            f[7] = f'v{ver}'
        con.executemany('INSERT INTO sc_campos (tabla, campo, tipo_sql, modulo, naturaleza, divulgacion, origen, '
                        'version_entrada, descripcion, documentado) VALUES (?,?,?,?,?,?,?,?,?,?)', filas_c)
        con.execute('UPDATE sc_version SET n_campos=(SELECT count(*) FROM sc_campos) WHERE version=?', (ver,))
        sha_o = sha256_archivo(archivo_origen) if archivo_origen and os.path.exists(archivo_origen) else None
        con.execute('INSERT INTO sc_modulos (modulo, prefijo, tabla, nivel, llave, temporal, vigencia_min, vigencia_max, '
                    'fuente, naturaleza, divulgacion, descripcion, archivo_origen, sha256_origen, filas, columnas, '
                    'cobertura, version_entrada, fecha, estado, reemplaza) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (modulo, prefijo, tabla, nivel, llave, int('vigencia' in df.columns), vmin, vmax, fuente, naturaleza,
                     divulgacion, descripcion, os.path.basename(archivo_origen) if archivo_origen else None, sha_o,
                     len(df), len(cols), cobertura, f'v{ver}', ahora(), 'vigente', reemplaza))
        if reemplaza:
            con.execute("UPDATE sc_modulos SET estado='reemplazado' WHERE modulo=?", (reemplaza,))
        con.execute('INSERT INTO sc_tablas (tabla, modulo, nivel, llave, tipo, geometria, filas, columnas, sha256, '
                    'congelada, version_entrada) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (tabla, modulo, nivel, llave if llave else ('COD_DANE_A' if 'COD_DANE_A' in df.columns else None),
                     'features' if tabla in gi else 'attributes', gi[tabla][1] if tabla in gi else None, len(df),
                     len(cols), huella_tabla(con, tabla), 1, f'v{ver}'))
        # el diccionario heredado también recibe las filas, para quien siga usándolo
        con.executemany('INSERT INTO diccionario (capa, campo, origen, descripcion) VALUES (?,?,?,?)',
                        [(f[0], f[1], f'{fuente} · módulo {modulo} (v{ver})', f[8]) for f in filas_c
                         if f[4] not in ('tecnico',)])
        con.commit()
    except BaseException:
        con.rollback()
        con.execute(f'DROP TABLE IF EXISTS "{tabla}"')
        con.execute('DELETE FROM gpkg_contents WHERE table_name=?', (tabla,))
        con.execute('DELETE FROM gpkg_geometry_columns WHERE table_name=?', (tabla,))
        con.commit()
        raise
    for x in avisos:
        print('AVISO:', x)
    print(f'módulo {modulo} cargado en {tabla}: {len(df)} filas, {len(cols)} columnas, '
          f'cobertura {cobertura:.1%}' if cobertura is not None else f'módulo {modulo} cargado en {tabla}: {len(df)} filas')
    print(f'versión {ver}')
    return ver


def cmd_agregar(a):
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    df, geom = leer_insumo(a)
    if a.columnas:
        keep = [c.strip() for c in a.columnas.split(',')]
        extra = [x for x in (a.llave, a.vigencia_col, geom) if x]
        df = df[[c for c in df.columns if c in keep or c in extra]]
    if a.pivotar_vigencias:
        anios = [c for c in df.columns if es_anio(c)]
        if not anios:
            sys.exit('--pivotar-vigencias: no hay columnas con nombre de año')
        idc = [c for c in df.columns if c not in anios]
        df = df.melt(id_vars=idc, value_vars=anios, var_name='vigencia', value_name=a.pivotar_vigencias)
        df['vigencia'] = df.vigencia.map(lambda x: int(float(x)))
        df = df.dropna(subset=[a.pivotar_vigencias])
    nivel = a.nivel
    if nivel in NIVELES:
        col = NIVELES[nivel]
        if nivel == 'ciudad' and not a.llave:
            df[col] = MPIO
        else:
            if not a.llave or a.llave not in df.columns:
                sys.exit(f'--llave: indique la columna de {nivel} (columnas: {list(df.columns)[:20]})')
            dom = dominio_llave(con, nivel) if nivel in ('upu', 'unidad_admin') else None
            df[col] = normalizar_llave(df[a.llave], nivel, dom)
            if a.llave != col:
                df = df.drop(columns=[a.llave])
    if a.vigencia_col:
        df = df.rename(columns={a.vigencia_col: 'vigencia'})
    elif a.vigencia is not None:
        df['vigencia'] = a.vigencia
    if nivel == 'punto' and geom and a.asignar_manzana is not None:
        df = asignar_manzana(con, a.base, df, a.asignar_manzana)
    meta = pd.read_csv(a.campos) if a.campos else None
    cargar_modulo(con, a.base, df, modulo=a.modulo, prefijo=a.prefijo, nivel=nivel, fuente=a.fuente,
                  naturaleza=a.naturaleza, divulgacion=a.divulgacion, descripcion=a.descripcion, campos_meta=meta,
                  archivo_origen=a.archivo, cobertura_minima=a.cobertura_minima,
                  descartar_sin_llave=a.descartar_sin_llave, forzar=a.forzar, reemplaza=a.reemplaza,
                  geometria=geom)


# ================================================================================================ puntos
def asignar_manzana(con, base, g, tolerancia):
    """Agrega COD_DANE_A a cada punto: manzana que lo contiene o la más cercana a `tolerancia` metros o menos."""
    import geopandas as gpd
    if g.crs is None:
        sys.exit('la capa de puntos no trae sistema de referencia')
    g = g.to_crs(SRS) if g.crs.to_epsg() != SRS else g
    mz = gpd.read_file(base, layer='manzanas', columns=['COD_DANE_A'], engine='pyogrio')
    g = g.drop(columns=[c for c in ('COD_DANE_A', 'asignacion', 'dist_asignacion_m') if c in g.columns])
    g = g.reset_index(drop=True)
    dentro = gpd.sjoin(g[[g.geometry.name]], mz, how='left', predicate='within')
    dentro = dentro[~dentro.index.duplicated()]
    g['COD_DANE_A'] = dentro.COD_DANE_A
    g['asignacion'] = np.where(g.COD_DANE_A.notna(), 'dentro', None)
    g['dist_asignacion_m'] = np.where(g.COD_DANE_A.notna(), 0.0, np.nan)
    faltan = g.COD_DANE_A.isna() & g.geometry.notna() & ~g.geometry.is_empty
    if faltan.any():
        cerca = gpd.sjoin_nearest(g.loc[faltan, [g.geometry.name]], mz, how='left', max_distance=tolerancia,
                                  distance_col='d')
        cerca = cerca[~cerca.index.duplicated()]
        g.loc[cerca.index, 'COD_DANE_A'] = cerca.COD_DANE_A
        g.loc[cerca.index, 'dist_asignacion_m'] = cerca.d
        g.loc[cerca.index[cerca.COD_DANE_A.notna()], 'asignacion'] = 'cercana'
    g['asignacion'] = g.asignacion.fillna('sin_asignar')
    print('asignación a manzana:', g.asignacion.value_counts().to_dict())
    return g


def cmd_puntos(a):
    import geopandas as gpd
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    if a.tabla not in geom_info(con):
        sys.exit(f'{a.tabla} no es una capa con geometría de la base')
    r = con.execute("SELECT divulgacion FROM sc_campos WHERE tabla=? AND naturaleza='geometria'", (a.tabla,)).fetchone()
    div_fuente = 'interno' if (a.tabla in TABLAS_INTERNAS or (r and r[0] == 'interno')) else 'publico'
    divulgacion = a.divulgacion or div_fuente
    if div_fuente == 'interno' and divulgacion != 'interno':
        sys.exit(f'{a.tabla} es de uso interno: sus conteos por manzana también lo son')
    g = gpd.read_file(a.base, layer=a.tabla, engine='pyogrio')
    if a.filtro:
        g = g.query(a.filtro)
    g = asignar_manzana(con, a.base, g, a.tolerancia)
    sin = int((g.asignacion == 'sin_asignar').sum())
    g = g[g.asignacion != 'sin_asignar']
    claves = ['COD_DANE_A']
    if a.anio_de:
        # corrección v2.4.5: un año entero (2021) leído con to_datetime se toma como nanosegundos desde 1970
        num = pd.to_numeric(g[a.anio_de], errors='coerce')
        if num.notna().mean() > 0.5 and num.dropna().between(1900, 2100).all():
            g['vigencia'] = num
        else:
            g['vigencia'] = pd.to_datetime(g[a.anio_de], errors='coerce').dt.year
        g = g[g.vigencia.notna()]
        g['vigencia'] = g.vigencia.astype(int)
        claves.append('vigencia')
    n = g.groupby(claves).size().rename('n').reset_index()
    todas = pd.read_sql('SELECT COD_DANE_A FROM sc_llaves', con)
    if a.anio_de:
        # solo los años que la fuente cubre: un año sin ningún registro es dato faltante, no cero
        anios = pd.DataFrame({'vigencia': sorted(int(v) for v in n.vigencia.unique())})
        todas = todas.merge(anios, how='cross')
    df = todas.merge(n, on=claves, how='left')
    df['n'] = df.n.fillna(0).astype(int)
    meta = pd.DataFrame([{'campo': f'{a.prefijo}_n', 'descripcion':
                          f'Registros de {a.tabla}{" (" + a.filtro + ")" if a.filtro else ""} asignados a la manzana '
                          f'(dentro o a {a.tolerancia:g} m o menos){" por año" if a.anio_de else ""}'}])
    cargar_modulo(con, a.base, df, modulo=a.modulo, prefijo=a.prefijo, nivel='manzana',
                  fuente=f'Derivado de {a.tabla} (sitecali puntos-a-manzana)', naturaleza='derivado',
                  divulgacion=divulgacion, descripcion=a.descripcion or f'Conteo de {a.tabla} por manzana',
                  campos_meta=meta, detalle_extra={'puntos_sin_asignar': sin, 'tolerancia_m': a.tolerancia,
                                                   'filtro': a.filtro})



# ================================================================================================ reorganizar
def sql_lit(v):
    return 'NULL' if v is None else ("'" + str(v).replace("'", "''") + "'")


def cmd_reorganizar(a):
    """Vuelve «larga por vigencia» una familia heredada sin copiar datos: crea una VISTA registrada como módulo."""
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    for chk, msg in ((re.fullmatch(r'[a-z][a-z0-9_]{2,40}', a.modulo), '--modulo inválido'),
                     (re.fullmatch(r'[a-z][a-z0-9]{1,6}', a.prefijo), '--prefijo inválido'),
                     (a.nivel in NIVELES, f'--nivel debe ser uno de {list(NIVELES)}'),
                     (a.naturaleza in NATURALEZAS[3:7], '--naturaleza inválida'),
                     (a.divulgacion in DIVULGACION, '--divulgacion inválida')):
        if not chk:
            sys.exit(msg)
    if con.execute('SELECT 1 FROM sc_modulos WHERE modulo=? OR prefijo=?', (a.modulo, a.prefijo)).fetchone():
        sys.exit('ya existe un módulo con ese nombre o prefijo')
    vista = f'm_{a.modulo}'
    canon = NIVELES[a.nivel]
    partes = []   # (tabla, año, {col_salida: col_origen})
    if a.tablas_patron:
        tablas = [(t, int(m.group(1))) for t in tablas_gpkg(con) if (m := re.fullmatch(a.tablas_patron, t))]
        if not tablas:
            sys.exit('ninguna tabla coincide con --tablas-patron')
        cols0 = [c for c, _ in columnas(con, tablas[0][0])]
        for t, _ in tablas:
            if [c for c, _ in columnas(con, t)] != cols0:
                sys.exit(f'{t} no tiene las mismas columnas que {tablas[0][0]}')
        valores = [c for c in cols0 if c not in ('fid', a.llave)]
        partes = [(t, y, {c: c for c in valores}) for t, y in sorted(tablas, key=lambda x: x[1])]
    elif a.tabla and a.columnas_anio:
        cols = [c for c, _ in columnas(con, a.tabla)]
        por_anio = {}
        for c in cols:
            m = re.fullmatch(a.columnas_anio, c)
            if m:
                por_anio.setdefault(int(m.group(2)), {})[m.group(1)] = c
        if not por_anio:
            sys.exit('ninguna columna coincide con --columnas-anio')
        raices = sorted({r for d in por_anio.values() for r in d})
        partes = [(a.tabla, y, {r: por_anio[y].get(r) for r in raices}) for y in sorted(por_anio)]
    else:
        sys.exit('indique --tablas-patron, o --tabla con --columnas-anio')
    origenes = sorted({p[0] for p in partes})
    for t in origenes:
        if a.llave not in [c for c, _ in columnas(con, t)]:
            sys.exit(f'{t} no tiene la columna {a.llave}')
    # llave canónica: en niveles de texto o numéricos se traduce con una expresión CASE fija
    crudos = pd.Series([r[0] for t in origenes for r in con.execute(f'SELECT DISTINCT "{a.llave}" FROM "{t}"')])
    dom = dominio_llave(con, a.nivel)
    if a.nivel in ('manzana', 'seccion', 'sector'):
        expr = f'"{a.llave}"'
        canonicos = crudos
    else:
        canonicos = normalizar_llave(crudos, a.nivel, dom if a.nivel in ('upu', 'unidad_admin') else None)
        expr = 'CASE "' + a.llave + '" ' + ' '.join(
            f'WHEN {sql_lit(c)} THEN {sql_lit(None if pd.isna(k) else (int(k) if a.nivel == "comuna" else k))}'
            for c, k in zip(crudos, canonicos) if c is not None) + ' END'
    en_dom = canonicos.isin(set(dom.tolist()))
    fuera = crudos[~en_dom].astype(str).tolist()
    filtro = ''
    if a.descartar_sin_llave and fuera:
        filtro = f' WHERE "{a.llave}" IN ({", ".join(sql_lit(c) for c in crudos[en_dom])})'
    selects = []
    for t, y, mapa in partes:
        cs = ', '.join(f'{(chr(34) + o + chr(34)) if o else "NULL"} AS "{a.prefijo}_{slug(s)}"' for s, o in mapa.items())
        selects.append(f'SELECT {y} * 100000 + fid AS fid, {expr} AS "{canon}", {y} AS vigencia, {cs} '
                       f'FROM "{t}"{filtro}')
    con.execute(f'CREATE VIEW "{vista}" AS ' + ' UNION ALL '.join(selects))
    try:
        dup = con.execute(f'SELECT count(*) FROM (SELECT "{canon}", vigencia FROM "{vista}" GROUP BY 1, 2 '
                          f'HAVING count(*) > 1)').fetchone()[0]
        if dup:
            raise SystemExit(f'la vista repite {dup} pares (llave, vigencia)')
        registrar_atributos(con, vista, a.descripcion)
        filas = con.execute(f'SELECT count(*) FROM "{vista}"').fetchone()[0]
        v_all = pd.read_sql(f'SELECT "{canon}" k FROM "{vista}"', con).k
        cobertura = float(v_all.isin(set(dom.tolist())).mean())
        meta = pd.read_csv(a.campos).set_index('campo') if a.campos else pd.DataFrame()
        cols_v = columnas(con, vista)
        sin_desc = []
        filas_c = []
        for c, ts in cols_v:
            if c == 'fid':
                nat, div, desc = 'tecnico', 'publico', 'Identificador de la fila en la vista (año × 100.000 + fid de origen)'
            elif c == canon:
                nat, div, desc = 'llave', 'publico', f'Llave canónica de {a.nivel} (sc_llaves.{canon})'
            elif c == 'vigencia':
                nat, div, desc = 'llave', 'publico', 'Año al que corresponde el dato'
            else:
                r = meta.loc[c] if c in meta.index else None
                nat = r['naturaleza'] if r is not None and 'naturaleza' in r and pd.notna(r['naturaleza']) else a.naturaleza
                div = r['divulgacion'] if r is not None and 'divulgacion' in r and pd.notna(r['divulgacion']) else a.divulgacion
                desc = r['descripcion'] if r is not None and pd.notna(r['descripcion']) else None
                if not desc:
                    sin_desc.append(c)
            filas_c.append([vista, c, ts or 'NUMERIC', a.modulo, nat, div, a.fuente, None, desc, int(bool(desc))])
        ys = [p[1] for p in partes]
        ver = subir_version(con, 'reorganizar', {'modulo': a.modulo, 'vista': vista, 'origen': origenes,
                                                 'vigencias': [min(ys), max(ys)], 'filas': filas,
                                                 'cobertura': cobertura, 'llaves_fuera': fuera[:20],
                                                 'descartadas': bool(filtro)})
        for f in filas_c:
            f[7] = f'v{ver}'
        con.executemany('INSERT INTO sc_campos (tabla, campo, tipo_sql, modulo, naturaleza, divulgacion, origen, '
                        'version_entrada, descripcion, documentado) VALUES (?,?,?,?,?,?,?,?,?,?)', filas_c)
        con.execute('INSERT INTO sc_modulos (modulo, prefijo, tabla, nivel, llave, temporal, vigencia_min, vigencia_max, '
                    'fuente, naturaleza, divulgacion, descripcion, archivo_origen, sha256_origen, filas, columnas, '
                    'cobertura, version_entrada, fecha, estado, reemplaza) VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (a.modulo, a.prefijo, vista, a.nivel, canon, min(ys), max(ys), a.fuente, a.naturaleza,
                     a.divulgacion, a.descripcion, 'vista sobre ' + ', '.join(origenes), None, filas, len(cols_v),
                     cobertura, f'v{ver}', ahora(), 'vigente', None))
        con.execute('INSERT INTO sc_tablas (tabla, modulo, nivel, llave, tipo, geometria, filas, columnas, sha256, '
                    'congelada, version_entrada) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (vista, a.modulo, a.nivel, canon, 'vista', None, filas, len(cols_v), huella_tabla(con, vista), 1,
                     f'v{ver}'))
        con.execute('UPDATE sc_version SET n_campos=(SELECT count(*) FROM sc_campos) WHERE version=?', (ver,))
        con.commit()
    except BaseException:
        con.rollback()
        con.execute(f'DROP VIEW IF EXISTS "{vista}"')
        con.execute('DELETE FROM gpkg_contents WHERE table_name=?', (vista,))
        con.commit()
        raise
    if fuera:
        print(f'AVISO: llaves de origen sin correspondencia en el territorio{" (excluidas)" if filtro else ""}: {fuera[:10]}')
    if sin_desc:
        print(f'AVISO: {len(sin_desc)} campos sin descripción: {sin_desc[:8]}')
    print(f'vista {vista}: {filas} filas × {len(cols_v)} columnas, vigencias {min(ys)}–{max(ys)}, '
          f'cobertura {cobertura:.1%}, sin copiar datos · versión {ver}')


# ================================================================================================ clasificar
def cmd_clasificar(a):
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    q, p = 'SELECT fid, campo FROM sc_campos WHERE tabla=?', [a.tabla]
    filas = con.execute(q, p).fetchall()
    if not filas:
        sys.exit(f'no hay campos catalogados de {a.tabla}')
    if a.campo:
        filas = [f for f in filas if f[1] == a.campo]
    elif a.patron:
        filas = [f for f in filas if re.search(a.patron, f[1])]
    if not filas:
        sys.exit('ningún campo coincide')
    sets, vals = [], []
    if a.naturaleza:
        if a.naturaleza not in NATURALEZAS:
            sys.exit(f'naturaleza: {NATURALEZAS}')
        sets.append('naturaleza=?'); vals.append(a.naturaleza)
    if a.divulgacion:
        if a.divulgacion not in DIVULGACION:
            sys.exit(f'divulgación: {DIVULGACION}')
        sets.append('divulgacion=?'); vals.append(a.divulgacion)
    if a.descripcion:
        sets.append('descripcion=?'); vals.append(a.descripcion)
        sets.append('documentado=1')
    if not sets:
        sys.exit('indique --naturaleza, --divulgacion o --descripcion')
    if a.divulgacion == 'publico' and not a.confirmo:
        sens = con.execute(f'SELECT campo FROM sc_campos WHERE fid IN ({",".join(str(f[0]) for f in filas)}) '
                           f"AND divulgacion != 'publico'").fetchall()
        if sens:
            sys.exit(f'{len(sens)} campos pasarían de interno o condicionado a público ({[s[0] for s in sens[:6]]}). '
                     f'Si es intencional, repita con --confirmo')
    con.execute(f'UPDATE sc_campos SET {", ".join(sets)} WHERE fid IN ({",".join(str(f[0]) for f in filas)})', vals)
    ver = subir_version(con, 'clasificar', {'tabla': a.tabla, 'campos': [f[1] for f in filas][:200],
                                            'naturaleza': a.naturaleza, 'divulgacion': a.divulgacion,
                                            'descripcion': a.descripcion})
    con.commit()
    print(f'{len(filas)} campos actualizados · versión {ver}')


# ================================================================================================ validar
def validar(con, rapido=False, verbose=True):
    errores, avisos = [], []
    exigir_inicializada(con)
    qc = con.execute('PRAGMA quick_check').fetchone()[0]
    if qc != 'ok':
        errores.append(f'quick_check: {qc}')
    reg = pd.read_sql('SELECT * FROM sc_tablas', con).set_index('tabla')
    actuales = [t for t in tablas_gpkg(con) if t not in SISTEMA]
    for t in sorted(set(actuales) - set(reg.index)):
        errores.append(f'tabla sin registrar: {t} (toda tabla nueva debe entrar con sitecali agregar)')
    for t in sorted(set(reg.index) - set(actuales)):
        errores.append(f'tabla registrada que ya no existe: {t}')
    camp = pd.read_sql('SELECT tabla, campo FROM sc_campos', con)
    camp_set = set(map(tuple, camp.values))
    gi = geom_info(con)
    for t in sorted(set(actuales) & set(reg.index)):
        r = reg.loc[t]
        cols = columnas(con, t)
        filas = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if r.congelada and (filas != r.filas or len(cols) != r.columnas):
            errores.append(f'{t}: cambió de {r.filas}×{r.columnas} a {filas}×{len(cols)} (está congelada)')
        elif r.congelada and not rapido and huella_tabla(con, t) != r.sha256:
            errores.append(f'{t}: el contenido cambió (huella SHA-256 distinta) y la tabla está congelada')
        nombres = {c for c, _ in cols}
        reg_c = {c for (tt, c) in camp_set if tt == t}
        if nombres - reg_c:
            errores.append(f'{t}: campos sin catalogar {sorted(nombres - reg_c)[:6]}')
        if reg_c - nombres:
            errores.append(f'{t}: campos catalogados que no existen {sorted(reg_c - nombres)[:6]}')
        if len(cols) > MAX_COLUMNAS:
            avisos.append(f'{t}: {len(cols)} columnas, cerca del límite de SQLite')
        if t in gi and gi[t][2] != SRS:
            errores.append(f'{t}: sistema de referencia {gi[t][2]} distinto de EPSG:{SRS}')
    mods = pd.read_sql("SELECT * FROM sc_modulos WHERE estado='vigente'", con)
    for _, m in mods.iterrows():
        if m.llave and m.nivel in NIVELES:
            dom = set(dominio_llave(con, m.nivel).tolist())
            v = pd.read_sql(f'SELECT "{m.llave}" k FROM "{m.tabla}"', con).k
            if m.nivel == 'comuna':
                v = pd.to_numeric(v, errors='coerce')
            cob = float(v.isin(dom).mean()) if len(v) else 0
            if m.cobertura is not None and cob + 1e-9 < m.cobertura:
                errores.append(f'módulo {m.modulo}: la cobertura bajó de {m.cobertura:.1%} a {cob:.1%}')
    nd = pd.read_sql("SELECT tabla, count(*) n FROM sc_campos WHERE documentado=0 GROUP BY tabla ORDER BY n DESC", con)
    if len(nd):
        avisos.append(f'{int(nd.n.sum())} campos sin descripción en {len(nd)} tablas (las primeras: '
                      f'{", ".join(f"{a} {b}" for a, b in nd.head(5).values)})')
    sc = con.execute("SELECT count(*) FROM sc_campos WHERE naturaleza='sin_clasificar'").fetchone()[0]
    if sc:
        avisos.append(f'{sc} campos con naturaleza sin_clasificar (heredados sin origen en el diccionario)')
    ncol = len(columnas(con, 'manzanas'))
    avisos.append(f'manzanas tiene {ncol} columnas y está congelada; lo nuevo entra en módulos')
    if verbose:
        print(f'versión {version_actual(con)} · {len(actuales)} capas y tablas · '
              f'{"verificación rápida (conteos y esquema)" if rapido else "verificación completa con huellas SHA-256"}')
        for e in errores:
            print('ERROR:', e)
        for x in avisos:
            print('aviso:', x)
        print('sin errores' if not errores else f'{len(errores)} errores')
    return errores, avisos


def cmd_validar(a):
    con = sqlite3.connect(a.base)
    errores, _ = validar(con, a.rapido)
    sys.exit(1 if errores else 0)


# ================================================================================================ catálogo
def resumen(con):
    v = version_actual(con)
    t = con.execute('SELECT count(*) FROM sc_tablas').fetchone()[0]
    c = pd.read_sql('SELECT naturaleza, divulgacion FROM sc_campos', con)
    print(f'SITECALI-Res v{v} · {t} capas y tablas registradas · {len(c)} campos catalogados')
    print('  por naturaleza:', c.naturaleza.value_counts().to_dict())
    print('  por divulgación:', c.divulgacion.value_counts().to_dict())


def cmd_catalogo(a):
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    resumen(con)
    mods = pd.read_sql('SELECT modulo, prefijo, nivel, temporal, vigencia_min, vigencia_max, filas, columnas, '
                       'cobertura, naturaleza, divulgacion, version_entrada, estado FROM sc_modulos', con)
    print(f'\nmódulos ({len(mods)}):')
    print(mods.to_string(index=False) if len(mods) else '  (ninguno)')
    hist = pd.read_sql('SELECT version, fecha, operacion FROM sc_version', con)
    print('\nbitácora:'); print(hist.to_string(index=False))
    cat = pd.read_sql('SELECT c.tabla, c.campo, c.naturaleza, c.divulgacion, t.nivel, c.version_entrada, c.origen, '
                      'c.descripcion FROM sc_campos c LEFT JOIN sc_tablas t USING (tabla)', con)
    if a.buscar:
        pat = a.buscar
        sel = cat[cat.campo.str.contains(pat, case=False, na=False) | cat.descripcion.str.contains(pat, case=False, na=False)]
        print(f'\n{len(sel)} campos con «{pat}»:')
        with pd.option_context('display.max_colwidth', 80, 'display.width', 200):
            print(sel.head(a.limite).to_string(index=False))
    if a.exportar:
        cat.to_csv(a.exportar, index=False)
        print('catálogo exportado:', a.exportar)


# ================================================================================================ marco
def resolver_campos(con, pedido):
    cat = pd.read_sql('SELECT c.tabla, c.campo, c.naturaleza, c.divulgacion, c.descripcion, t.nivel, t.llave '
                      'FROM sc_campos c JOIN sc_tablas t USING (tabla)', con)
    elegidos = []
    for item in [x.strip() for x in pedido.split(',') if x.strip()]:
        agg = None
        if ':' in item:
            item, agg = item.split(':', 1)
        tabla, campo = (item.split('.', 1) if '.' in item else (None, item))
        sel = cat[cat.campo.map(lambda c: fnmatch.fnmatchcase(c, campo))]
        if tabla:
            sel = sel[sel.tabla == tabla]
        sel = sel[~sel.naturaleza.isin(['geometria', 'tecnico'])]
        if sel.empty:
            sys.exit(f'no encuentro el campo {item}')
        if not tabla and sel.tabla.nunique() > 1 and '*' not in campo:
            pref = sel[sel.tabla == 'manzanas']
            if len(pref) == 1:
                sel = pref
            else:
                sys.exit(f'{item} está en varias tablas ({sel.tabla.unique().tolist()[:8]}): use tabla.campo')
        for _, r in sel.iterrows():
            elegidos.append({**r.to_dict(), 'agregacion': agg})
    return pd.DataFrame(elegidos).drop_duplicates(['tabla', 'campo'])


def cmd_marco(a):
    con = sqlite3.connect(a.base)
    exigir_inicializada(con)
    sel = resolver_campos(con, a.campos)
    sel = sel[sel.naturaleza != 'llave'] if len(sel[sel.naturaleza != 'llave']) else sel
    if a.publico:
        malos = sel[sel.divulgacion == 'interno']
        if len(malos):
            sys.exit(f'--publico: estos campos son de uso interno y no se exportan: '
                     f'{(malos.tabla + "." + malos.campo).tolist()[:12]}')
    sin_llave = sel[~sel.nivel.isin(list(NIVELES))]
    if len(sin_llave):
        sys.exit(f'las tablas {sin_llave.tabla.unique().tolist()} no tienen una fila por unidad territorial '
                 f'(nivel punto, registro u otro). Para puntos use puntos-a-manzana.')
    L = a.nivel
    kL = NIVELES[L]
    ll = pd.read_sql('SELECT * FROM sc_llaves', con).drop(columns=['fid'])
    ll['comuna_n'] = ll.comuna_n.astype('Int64')
    if L == 'manzana':
        marco = ll[['COD_DANE_A', 'seccion', 'sector', 'id_barrio', 'barrio_u', 'comuna_n', 'unidad_admin',
                    'upu']].copy()
    else:
        marco = ll[[kL]].dropna().drop_duplicates().reset_index(drop=True)
    tablas_temporales = []
    for t in sel.tabla.unique():
        cols_t = [c for c, _ in columnas(con, t)]
        if 'vigencia' in cols_t:
            tablas_temporales.append(t)
    if tablas_temporales and a.vigencia is None:
        sys.exit(f'{tablas_temporales} tienen varias vigencias: indique --vigencia AÑO o --vigencia todas')
    if a.vigencia == 'todas' and tablas_temporales:
        vig = sorted({int(v) for t in tablas_temporales
                      for (v,) in con.execute(f'SELECT DISTINCT vigencia FROM "{t}"') if v is not None})
        marco = marco.merge(pd.DataFrame({'vigencia': vig}), how='cross')
    meta_salida = []
    for t, grupo in sel.groupby('tabla', sort=False):
        nivel_t, llave_t = grupo.nivel.iloc[0], grupo.llave.iloc[0]
        kT = NIVELES[nivel_t]
        cols_t = [c for c, _ in columnas(con, t)]
        temporal = 'vigencia' in cols_t
        campos_t = grupo.campo.tolist()
        extra = ['publicable'] if a.publico and 'publicable' in cols_t and 'publicable' not in campos_t else []
        q = f'SELECT "{llave_t}" AS __k, {", ".join(chr(34) + c + chr(34) for c in campos_t + extra)}' + \
            (', vigencia' if temporal else '') + f' FROM "{t}"'
        par = []
        if temporal and a.vigencia not in (None, 'todas'):
            q += ' WHERE vigencia = ?'
            par = [int(a.vigencia)]
        d = pd.read_sql(q, con, params=par)
        d['__k'] = normalizar_llave(d['__k'], nivel_t, dominio_llave(con, nivel_t) if nivel_t in ('upu', 'unidad_admin') else None)
        if a.publico:
            for _, r in grupo.iterrows():
                if r.divulgacion == 'condicionado':
                    if 'publicable' not in d.columns:
                        sys.exit(f'--publico: {t}.{r.campo} es condicionado y la tabla no trae la columna publicable')
                    d.loc[d.publicable.astype(str).str.lower().isin(['0', 'false', 'none', 'nan']), r.campo] = np.nan
            d = d.drop(columns=[c for c in extra if c in d.columns])
        nombres = {c: (c if c not in marco.columns else f'{t}__{c}') for c in campos_t}
        d = d.rename(columns=nombres)
        on = ['__k'] + (['vigencia'] if temporal and a.vigencia == 'todas' else [])
        if temporal and a.vigencia not in (None, 'todas'):
            d = d.drop(columns=['vigencia'])
        if kT == kL:
            marco = marco.merge(d.rename(columns={'__k': kL}), on=[kL] + on[1:], how='left')
        else:
            mapa = ll[[kL, kT]].dropna().drop_duplicates()
            if mapa.groupby(kL)[kT].nunique().max() <= 1:      # la tabla es más gruesa: se replica hacia abajo
                tmp = mapa.merge(d.rename(columns={'__k': kT}), on=kT, how='left').drop(columns=[kT])
                marco = marco.merge(tmp, on=[kL] + on[1:], how='left')
            elif mapa.groupby(kT)[kL].nunique().max() <= 1:    # la tabla es más fina: hay que agregar
                d = d.rename(columns={'__k': kT}).merge(mapa, on=kT, how='inner')
                specs = {nombres[r['campo']]: r['agregacion'] for _, r in grupo.iterrows()}
                faltan = [c for c, s in specs.items() if not isinstance(s, str) or not s]
                if faltan:
                    sys.exit(f'para llevar {faltan[:5]} de {nivel_t} a {L} indique la agregación: campo:suma, '
                             f'campo:media o campo:media@peso (el peso debe ser otro campo de la misma tabla)')
                grp = [kL] + on[1:]
                partes = []
                for c, s in specs.items():
                    if s == 'suma':
                        partes.append(d.groupby(grp)[c].sum(min_count=1))
                    elif s == 'media':
                        partes.append(d.groupby(grp)[c].mean())
                    elif s.startswith('media@'):
                        w = s.split('@', 1)[1]
                        if w not in cols_t:
                            sys.exit(f'el peso {w} no está en {t}')
                        pw = pd.read_sql(f'SELECT "{llave_t}" __k, "{w}" __w FROM "{t}"', con)
                        pw['__k'] = normalizar_llave(pw['__k'], nivel_t)
                        dd = d.merge(pw.rename(columns={'__k': kT}), on=kT, how='left')
                        dd['__p'] = dd[c] * dd['__w']
                        g = dd.groupby(grp)
                        partes.append((g['__p'].sum(min_count=1) / g['__w'].sum()).rename(c))
                    else:
                        sys.exit(f'agregación desconocida: {s}')
                marco = marco.merge(pd.concat(partes, axis=1).reset_index(), on=grp, how='left')
            else:
                sys.exit(f'{nivel_t} y {L} no están anidados: no se puede unir {t} sin una regla de reparto')
        for _, r in grupo.iterrows():
            meta_salida.append({'columna': nombres[r.campo], 'tabla': t, 'campo': r.campo, 'nivel_origen': nivel_t,
                                'naturaleza': r.naturaleza, 'divulgacion': r.divulgacion, 'agregacion': r['agregacion'],
                                'descripcion': r.descripcion})
    if len(marco.columns) > MAX_COLUMNAS:
        sys.exit(f'el marco tendría {len(marco.columns)} columnas: pida menos campos')
    salida = a.salida
    if salida.endswith('.gpkg'):
        if L != 'manzana':
            sys.exit('la salida .gpkg solo se arma por manzana (use .csv o .parquet para otros niveles)')
        import geopandas as gpd
        mz = gpd.read_file(a.base, layer='manzanas', columns=['COD_DANE_A'], engine='pyogrio')
        gpd.GeoDataFrame(mz.merge(marco, on='COD_DANE_A', how='right'), geometry='geometry', crs=mz.crs) \
            .to_file(salida, layer='marco', driver='GPKG', engine='pyogrio')
    elif salida.endswith('.parquet'):
        marco.to_parquet(salida, index=False)
    else:
        marco.to_csv(salida, index=False)
    pd.DataFrame(meta_salida).to_csv(re.sub(r'\.(csv(\.gz)?|parquet|gpkg)$', '', salida) + '.campos.csv', index=False)
    print(f'marco por {L}: {len(marco)} filas × {len(marco.columns)} columnas → {salida} '
          f'(versión {version_actual(con)}{", solo campos publicables" if a.publico else ""})')
    if a.publico and sel.divulgacion.eq('condicionado').any():
        print('Los campos condicionados quedaron vacíos donde la unidad no es publicable.')
    if a.publico:
        print('Recuerde: un marco con solo campos públicos no garantiza por sí mismo la regla de 5 víctimas y '
              '1.000 residentes en agregados que usted arme después.')


# ================================================================================================ empaquetar
def cmd_empaquetar(a):
    con = sqlite3.connect(a.base)
    errores, avisos = validar(con, rapido=False)
    if errores:
        sys.exit('no se empaqueta una base con errores de validación')
    v = version_actual(con)
    os.makedirs(a.carpeta, exist_ok=True)
    nombre = f'base_integrada_cali_v{v.replace(".", "")}'
    cat = pd.read_sql('SELECT c.tabla, c.campo, c.tipo_sql, c.naturaleza, c.divulgacion, t.nivel, c.modulo, '
                      'c.version_entrada, c.origen, c.descripcion FROM sc_campos c LEFT JOIN sc_tablas t USING (tabla)', con)
    cat.to_csv(os.path.join(a.carpeta, f'{nombre}_catalogo.csv'), index=False)
    mods = pd.read_sql('SELECT * FROM sc_modulos', con)
    hist = pd.read_sql('SELECT version, fecha, operacion, detalle FROM sc_version', con)
    tabs = pd.read_sql('SELECT tabla, modulo, nivel, filas, columnas, congelada, version_entrada FROM sc_tablas', con)
    con.close()
    gz = os.path.join(a.carpeta, f'{nombre}.gpkg.zip')
    with zipfile.ZipFile(gz, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(a.base, arcname=f'{nombre}.gpkg')
    shutil.copy(os.path.abspath(__file__), os.path.join(a.carpeta, 'sitecali.py'))
    man = {'version': v, 'fecha': ahora(), 'gpkg': f'{nombre}.gpkg', 'sha256_gpkg': sha256_archivo(a.base),
           'zip': os.path.basename(gz), 'sha256_zip': sha256_archivo(gz), 'tablas': len(tabs),
           'campos': len(cat), 'modulos': mods.modulo.tolist()}
    with open(os.path.join(a.carpeta, 'MANIFIESTO.json'), 'w', encoding='utf-8') as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    lin = [f'# SITECALI-Res · base integrada v{v}', '',
           f'Generado por `sitecali.py empaquetar` el {man["fecha"]}. {len(tabs)} capas y tablas y {len(cat)} campos '
           f'catalogados. La v2.3.4 está congelada con huellas SHA-256; lo posterior entra como módulos.', '',
           f'Archivo de la base: `{nombre}.gpkg` (SHA-256 `{man["sha256_gpkg"]}`).', '',
           '## Módulos', '', '| Módulo | Prefijo | Nivel | Vigencias | Filas | Naturaleza | Divulgación | Fuente | Versión | Estado |',
           '|---|---|---|---|---|---|---|---|---|---|']
    for _, m in mods.iterrows():
        vig = f'{m.vigencia_min}–{m.vigencia_max}' if m.temporal else '—'
        lin.append(f'| `{m.modulo}` | `{m.prefijo}_` | {m.nivel} | {vig} | {m.filas} | {m.naturaleza} | '
                   f'{m.divulgacion} | {m.fuente} | {m.version_entrada} | {m.estado} |')
    lin += ['', '## Bitácora', '', '| Versión | Fecha | Operación |', '|---|---|---|']
    lin += [f'| {r.version} | {r.fecha} | {r.operacion} |' for r in hist.itertuples()]
    lin += ['', '## Campos por naturaleza y divulgación', '',
            tabla_md(cat.groupby(['naturaleza', 'divulgacion']).size().unstack(fill_value=0)), '',
            'Las reglas de uso de la v2.3.4 siguen vigentes (`claude/v234_LEEME.md`). Ningún campo `interno` sale de '
            'la base; los `condicionado` solo donde la columna `publicable` es verdadera.']
    with open(os.path.join(a.carpeta, f'LEEME_v{v}.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lin) + '\n')
    print(f'paquete v{v} en {a.carpeta}: {os.path.basename(gz)} ({os.path.getsize(gz) / 1e6:.0f} MB), catálogo, '
          f'LEEME, MANIFIESTO.json y sitecali.py')


def tabla_md(t):
    cab = ['naturaleza'] + [str(c) for c in t.columns]
    filas = ['| ' + ' | '.join(cab) + ' |', '|' + '---|' * len(cab)]
    filas += ['| ' + ' | '.join([str(i)] + [str(v) for v in r]) + ' |' for i, r in zip(t.index, t.values)]
    return '\n'.join(filas)


# ================================================================================================ CLI
def main():
    p = argparse.ArgumentParser(prog='sitecali.py', description='Mecanismo de crecimiento de SITECALI-Res')
    s = p.add_subparsers(dest='cmd', required=True)
    x = s.add_parser('inicializar', help='congela la v2.3.4 y crea el catálogo (v2.4.0)')
    x.add_argument('base'); x.add_argument('salida'); x.add_argument('--sobrescribir', action='store_true')
    x.set_defaults(f=cmd_inicializar)

    x = s.add_parser('agregar', help='carga un módulo nuevo')
    x.add_argument('base'); x.add_argument('archivo')
    x.add_argument('--modulo', required=True); x.add_argument('--prefijo', required=True)
    x.add_argument('--nivel', required=True, choices=list(NIVELES) + ['punto', 'otro'])
    x.add_argument('--llave', help='columna del archivo con la llave del nivel')
    x.add_argument('--vigencia-col', help='columna con el año'); x.add_argument('--vigencia', type=int, help='año fijo')
    x.add_argument('--pivotar-vigencias', metavar='NOMBRE', help='las columnas con nombre de año pasan a filas')
    x.add_argument('--fuente', required=True); x.add_argument('--descripcion', required=True)
    x.add_argument('--naturaleza', default='observado'); x.add_argument('--divulgacion', default='publico')
    x.add_argument('--campos', help='CSV con campo, descripcion[, naturaleza, divulgacion]')
    x.add_argument('--columnas', help='solo estas columnas (separadas por coma)')
    x.add_argument('--hoja'); x.add_argument('--encabezado', type=int, default=0); x.add_argument('--capa')
    x.add_argument('--sep', default=None); x.add_argument('--codificacion', default='utf-8')
    x.add_argument('--asignar-manzana', type=float, metavar='METROS', help='en puntos: agrega COD_DANE_A')
    x.add_argument('--cobertura-minima', type=float, default=0.95)
    x.add_argument('--descartar-sin-llave', action='store_true'); x.add_argument('--forzar', action='store_true')
    x.add_argument('--reemplaza', help='módulo anterior que queda como reemplazado (no se borra)')
    x.set_defaults(f=cmd_agregar)

    x = s.add_parser('puntos-a-manzana', help='cuenta una capa de puntos por manzana (y por año) como módulo')
    x.add_argument('base'); x.add_argument('--tabla', required=True)
    x.add_argument('--modulo', required=True); x.add_argument('--prefijo', required=True)
    x.add_argument('--tolerancia', type=float, default=150); x.add_argument('--anio-de')
    x.add_argument('--filtro', help='expresión de pandas.query, p. ej. "tipo == \'oficial\'"')
    x.add_argument('--divulgacion'); x.add_argument('--descripcion')
    x.set_defaults(f=cmd_puntos)

    x = s.add_parser('reorganizar', help='vuelve larga por vigencia una familia heredada, como vista (sin copiar)')
    x.add_argument('base'); x.add_argument('--modulo', required=True); x.add_argument('--prefijo', required=True)
    x.add_argument('--nivel', required=True); x.add_argument('--llave', required=True)
    x.add_argument('--tablas-patron', help=r'regex con el año en el grupo 1, p. ej. "pob_edad_sexo_(\d{4})"')
    x.add_argument('--tabla'); x.add_argument('--columnas-anio', help=r'regex raíz y año, p. ej. "(.+)_(\d{4})"')
    x.add_argument('--fuente', required=True); x.add_argument('--descripcion', required=True)
    x.add_argument('--naturaleza', default='derivado'); x.add_argument('--divulgacion', default='publico')
    x.add_argument('--campos'); x.add_argument('--descartar-sin-llave', action='store_true')
    x.set_defaults(f=cmd_reorganizar)

    x = s.add_parser('clasificar', help='corrige naturaleza, divulgación o descripción de campos')
    x.add_argument('base'); x.add_argument('--tabla', required=True)
    x.add_argument('--campo'); x.add_argument('--patron')
    x.add_argument('--naturaleza'); x.add_argument('--divulgacion'); x.add_argument('--descripcion')
    x.add_argument('--confirmo', action='store_true', help='necesario para volver público algo interno')
    x.set_defaults(f=cmd_clasificar)

    x = s.add_parser('validar', help='integridad, congelamiento, catálogo y llaves')
    x.add_argument('base'); x.add_argument('--rapido', action='store_true'); x.set_defaults(f=cmd_validar)

    x = s.add_parser('catalogo', help='resumen, módulos, bitácora y búsqueda de campos')
    x.add_argument('base'); x.add_argument('--buscar'); x.add_argument('--exportar')
    x.add_argument('--limite', type=int, default=60); x.set_defaults(f=cmd_catalogo)

    x = s.add_parser('marco', help='arma una tabla de análisis con campos de la base y de los módulos')
    x.add_argument('base'); x.add_argument('--campos', required=True)
    x.add_argument('--nivel', default='manzana', choices=list(NIVELES))
    x.add_argument('--vigencia', help='AÑO o todas'); x.add_argument('--publico', action='store_true')
    x.add_argument('-o', '--salida', required=True); x.set_defaults(f=cmd_marco)

    x = s.add_parser('empaquetar', help='valida y arma el paquete de la versión (zip, catálogo, LEEME, manifiesto)')
    x.add_argument('base'); x.add_argument('carpeta'); x.set_defaults(f=cmd_empaquetar)
    a = p.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
