"""Exporta al observatorio solo datos de nivel público: marcos públicos, módulos transformados (k >= 5) y opa_.

Uso: exportar_observatorio.py BASE.gpkg TRANSFORMADOS.gpkg CARPETA_MODULOS OPA.csv [SALIDA.json]
  TRANSFORMADOS.gpkg  resultados/transformados.gpkg del paquete de traspaso (o la salida de la etapa 35)
  CARPETA_MODULOS     ajustes/modulos del paquete (bordes_manzana.csv, tasa_homicidio_barrio.csv)
  OPA.csv             salida de analisis/oportunidades_activos.py (con su .tipologias.csv al lado)
No lee ningún módulo interno: el resultado puede publicarse.
"""
import json, sqlite3, sys
import numpy as np, pandas as pd, pyogrio, shapely
B, T, M, OPA = sys.argv[1:5]
SALIDA = sys.argv[5] if len(sys.argv) > 5 else 'datos.json'
M = M.rstrip('/') + '/'
con = sqlite3.connect(f'file:{B}?mode=ro', uri=True)
g = pyogrio.read_dataframe(B, layer='manzanas', columns=['COD_DANE_A', 'barrio', 'id_barrio', 'comuna', 'estrato_moda',
    'pob18_v196', 'det_mecanismo', 'det_n_mecanismos', 'det_precariedad_pct', 'det_envejecimiento_pct',
    'det_inquilinato_pct', 'det_densificacion_pct'])
f = pd.read_sql('select COD_DANE_A, fys_idx_fisico, fys_idx_social, fys_cuadrante from m_fisico_social_2025', con).drop_duplicates('COD_DANE_A')
o = pd.read_csv(OPA, dtype={'COD_DANE_A': str})
bd = pd.read_csv(M + 'bordes_manzana.csv', dtype={'COD_DANE_A': str})
th = pd.read_csv(M + 'tasa_homicidio_barrio.csv', dtype={'id_barrio': str})
g = g.merge(f, on='COD_DANE_A', how='left').merge(o, on='COD_DANE_A', how='left').merge(
    bd[['COD_DANE_A', 'tipo_borde', 'frente_arterial', 'dist_arterial_m', 'esquinas_criticas_50m', 'tramo_principal']], on='COD_DANE_A', how='left')
x0, y0, x1, y1 = g.total_bounds
x0, y0 = np.floor(x0), np.floor(y0)

def cod(geom, tol):
    geom = shapely.simplify(geom, tol, preserve_topology=True)
    partes = []
    polys = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    for p in polys:
        if p.geom_type == 'Polygon':
            c = np.asarray(p.exterior.coords)
        else:
            c = np.asarray(p.coords)
        q = np.round((c - [x0, y0]) / 2).astype(int)   # cuantizado a 2 m
        q = q[np.r_[True, (np.diff(q, axis=0) != 0).any(1)]]
        if len(q) >= 2:
            partes.append(q.ravel().tolist())
    return partes

def cat(s):
    v = sorted(s.dropna().unique().tolist()); idx = {k: i for i, k in enumerate(v)}
    return v, [idx.get(k, -1) if isinstance(k, str) else -1 for k in s]

def num(s, d=1):
    return [None if pd.isna(v) else round(float(v), d) for v in s]

D = {'origen': [x0, y0], 'escala': 2, 'n': len(g)}
D['geom'] = [cod(p, 2.5) for p in g.geometry]
for c, d in [('pob18_v196', 0), ('estrato_moda', 0), ('oportunidades_pct', 0), ('activos_pct', 0), ('oportunidades_geom', 0),
             ('o_educacion', 2), ('o_salud', 2), ('o_trabajo_ciudad', 2), ('o_espacio_publico', 2), ('o_vida_barrio', 2),
             ('a_capital_humano', 2), ('a_trabajo', 2), ('a_vivienda_conectividad', 2), ('privaciones_oportunidad', 0),
             ('det_precariedad_pct', 0), ('det_envejecimiento_pct', 0), ('det_inquilinato_pct', 0), ('det_densificacion_pct', 0),
             ('det_n_mecanismos', 0), ('fys_idx_fisico', 2), ('fys_idx_social', 2), ('dist_arterial_m', 0),
             ('esquinas_criticas_50m', 0), ('tramo_principal', 0)]:
    D[c] = num(g[c], d)
for c in ['barrio', 'comuna', 'desajuste', 'tipo_hogar', 'det_mecanismo', 'fys_cuadrante', 'tipo_borde',
          'oportunidades_robustez', 'activos_robustez']:
    D[c + '_cat'], D[c] = cat(g[c].replace('', np.nan))
D['cod'] = g.COD_DANE_A.tolist()
D['id_barrio'] = g.id_barrio.fillna('').tolist()
th = th[['id_barrio', 'residentes_2018', 'tasa_eb', 'ic_inf', 'ic_sup', 'estado', 'advertencia']]
D['barrio_tasa'] = {r.id_barrio: [None if pd.isna(r.tasa_eb) else r.tasa_eb, None if pd.isna(r.ic_inf) else r.ic_inf,
                    None if pd.isna(r.ic_sup) else r.ic_sup, r.estado, '' if pd.isna(r.advertencia) else r.advertencia,
                    None if pd.isna(r.residentes_2018) else float(r.residentes_2018)] for r in th.itertuples()}
tr = pyogrio.read_dataframe(T, layer='fronteras_manzana_tramos')
urb = shapely.union_all(shapely.buffer(g.geometry[g.oportunidades_pct.notna()].values, 80))
tr['geometry'] = shapely.intersection(tr.geometry.values, urb)
tr['tipo'] = np.where(tr.r_fys_idx_social > 1.5, 'frontera', np.where(tr.r_fys_idx_social < 1, 'costura', 'intermedio'))
D['tramos'] = [{'id': int(r.tramo_id), 'tipo': r.tipo, 'r': None if pd.isna(r.r_fys_idx_social) else round(float(r.r_fys_idx_social), 3), 'pares': int(r.n_pares),
                'barrios': r.barrios, 'g': cod(r.geometry, 5)} for r in tr.itertuples()]
rj = pyogrio.read_dataframe(T, layer='homicidio_2018_2024')
D['rejilla'] = [{'n': int(r.n), 't': int(r.tam_m), 'd': float(r.densidad_km2), 'g': cod(r.geometry, 1)} for r in rj.itertuples()]
# resumen de tipologías para la leyenda
D['tipologias'] = pd.read_csv(OPA.replace('.csv', '.tipologias.csv'), index_col=0).round(1).reset_index().to_dict('list')
s = json.dumps(D, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
open(SALIDA, 'w').write(s)
print(len(s) / 1e6, 'MB', x0, y0, (x1 - x0) / 2, (y1 - y0) / 2)
print(pd.Series(D['desajuste']).value_counts(), D['desajuste_cat'], D['tipo_hogar_cat'])
