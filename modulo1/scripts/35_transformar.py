#!/usr/bin/env python3
"""Etapa 35. Tercera puerta: lo que sale transformado.

Toma datos de uso interno (puntos de homicidio, lesionados, incidentes, visitas de riesgo) y produce patrones
publicables según config/transformaciones.conf. El registro individual no sale; el patrón sí:
  rejilla     partición adaptativa en árbol: tan fina como lo permita la regla de k eventos por hoja
  esquinas    conteos por esquina, con supresión complementaria frente a la rejilla asociada
  tasa        tasas por barrio o sección con Bayes empírico e intervalo; el umbral de residentes advierte, no censura
  conteo      conteos por unidad y dimensión (visitas de riesgo por barrio y fenómeno)
  fronteras   bordes entre barrios vecinos: contraste entre los dos lados y qué hay sobre el borde, con prueba
  corredores  concentración de eventos sobre vías estructurantes frente a su peso en la red vial

Todo conteo publicado es 0 o al menos k. Ningún producto lleva fecha, hora, edad, sexo ni atributos individuales.
Cada corrida rehace los productos desde los puntos originales.

Denominadores (regla de población de la v2.5): las tasas de un periodo van como eventos sobre la suma de pr_total
de cada año del periodo (años-persona, m_poblacion_referencia_v25), nunca sobre la población de 2018 multiplicada
por el número de años. Las secciones son las 945 analíticas (scm_analitica) y la comuna es ter_comuna.
Uso: 35_transformar.py BASE.gpkg CONFIG_DIR DESTINO_DIR
"""
import configparser
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from scipy import stats
from shapely.geometry import box
from shapely.ops import unary_union

SRS = 'EPSG:9377'
DICC = []        # diccionario de campos de los productos
REGISTRO = []    # parámetros y balance de cada producto


def slug(texto):
    t = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')


def describir(objeto, columnas, origen, nivel):
    for col, desc in columnas.items():
        DICC.append({'objeto': objeto, 'columna': col, 'tabla': origen, 'campo': col, 'nivel_origen': nivel,
                     'naturaleza': 'derivado', 'divulgacion': 'publico', 'agregacion': 'transformado',
                     'descripcion': desc})


def registrar(objeto, tipo, params, total, publicados, k):
    REGISTRO.append({'objeto': objeto, 'tipo': tipo, 'k': k, 'eventos_total': int(total),
                     'eventos_publicados': int(publicados), 'eventos_suprimidos': int(total - publicados),
                     'pct_publicado': round(100 * publicados / total, 1) if total else None,
                     'parametros': json.dumps(params, ensure_ascii=False)})


def puntos(base, capa, campo_anio, desde, hasta, columnas=()):
    g = gpd.read_file(base, layer=capa, columns=[campo_anio, *columnas], engine='pyogrio')
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g[(g[campo_anio] >= desde) & (g[campo_anio] <= hasta)].reset_index(drop=True)
    return g


def suprimir(df, grupo, valor, k, total_conocido=True):
    """Supresión primaria (1..k-1) y complementaria dentro de cada grupo.

    Si el total del grupo es conocido, lo suprimido dentro de él tiene que sumar 0 o al menos k; si no, se
    suprimen además las unidades publicadas más pequeñas (con eventos) hasta cumplirlo. Devuelve una serie
    con el motivo: '' (publicado), 'menos de k' o 'complementaria'.
    """
    motivo = pd.Series('', index=df.index)
    motivo[(df[valor] > 0) & (df[valor] < k)] = 'menos de k'
    if not total_conocido:
        return motivo
    for _, idx in df.groupby(grupo, dropna=False).groups.items():
        sub = df.loc[idx]
        s = sub.loc[motivo[idx] != '', valor].sum()
        candidatos = sub[(motivo[idx] == '') & (sub[valor] > 0)].sort_values(valor).index.tolist()
        while 0 < s < k and candidatos:
            c = candidatos.pop(0)
            motivo[c] = 'complementaria'
            s += df.at[c, valor]
    return motivo


# ------------------------------------------------------------------------------------------- rejilla adaptativa
def particion(x, y, tamanos, k):
    hojas, suprimidos = [], 0

    def cuadro(i, j, s):
        return box(i * s, j * s, (i + 1) * s, (j + 1) * s)

    def hijos(idx, s):
        i = np.floor(x[idx] / s).astype(np.int64)
        j = np.floor(y[idx] / s).astype(np.int64)
        return {c: v.to_numpy() for c, v in pd.Series(idx).groupby([i, j])}

    def hoja(claves, idx, s, fusion):
        hojas.append({'geometry': unary_union([cuadro(a, b, s) for a, b in claves]), 'n': len(idx),
                      'tam_m': s, 'fusion': int(fusion), 'idx': idx})

    def dividir(clave, idx, nivel):
        if nivel == len(tamanos) - 1:
            hoja([clave], idx, tamanos[nivel], False)
            return
        s2 = tamanos[nivel + 1]
        h = hijos(idx, s2)
        grandes = {c: v for c, v in h.items() if len(v) >= k}
        chicos = {c: v for c, v in h.items() if len(v) < k}
        n_chicos = sum(len(v) for v in chicos.values())
        if n_chicos == 0 or n_chicos >= k:
            if chicos:
                hoja(list(chicos), np.concatenate(list(chicos.values())), s2, True)
            for c, v in grandes.items():
                dividir(c, v, nivel + 1)
        else:  # las chicas no alcanzan k juntas: se funden con la hermana grande más pequeña
            c0 = min(grandes, key=lambda c: len(grandes[c]))
            hoja(list(chicos) + [c0], np.concatenate(list(chicos.values()) + [grandes[c0]]), s2, True)
            for c, v in grandes.items():
                if c != c0:
                    dividir(c, v, nivel + 1)

    for c, v in hijos(np.arange(len(x)), tamanos[0]).items():
        if len(v) >= k:
            dividir(c, v, 0)
        else:
            suprimidos += len(v)
    return hojas, suprimidos


def rejilla(nombre, sec, g, base, rep_manzanas, salida):
    k, tamanos = g['k'], g['tamanos']
    pts = puntos(base, sec['capa'], sec['campo_anio'], int(sec['desde']), int(sec['hasta']))
    x, y = pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy()
    hojas, suprimidos = particion(x, y, tamanos, k)
    gdf = hojas_a_gdf(hojas, sec, rep_manzanas)
    assert gdf.n.sum() + suprimidos == len(pts) and (gdf.n >= k).all()
    salida[nombre] = gdf
    describir(nombre, cols_rejilla(sec, k), sec['capa'], 'rejilla adaptativa')
    registrar(nombre, 'rejilla', {**dict(sec), 'tamanos_m': tamanos}, len(pts), gdf.n.sum(), k)
    print(f'{nombre}: {len(pts)} eventos, {len(gdf)} hojas, {100 * gdf.n.sum() / len(pts):.1f} % publicado; '
          'eventos por resolución ' + resumen_resolucion(gdf, len(pts)))
    return pts


def resumen_resolucion(gdf, total):
    res = gdf.groupby('tam_m').n.sum().sort_index()
    return ', '.join(f'{int(s)} m: {100 * v / total:.0f} %' for s, v in res.items())


def hojas_a_gdf(hojas, sec, rep_manzanas):
    anios = int(sec['hasta']) - int(sec['desde']) + 1
    gdf = gpd.GeoDataFrame([{c: h[c] for c in ('geometry', 'n', 'tam_m', 'fusion')} for h in hojas],
                           geometry='geometry', crs=SRS)
    gdf['eventos_por_anio'] = (gdf.n / anios).round(2)
    gdf['area_km2'] = (gdf.area / 1e6).round(4)
    gdf['densidad_km2'] = (gdf.n / gdf.area_km2).round(1)
    j = gpd.sjoin(rep_manzanas, gdf[['geometry']], predicate='within', how='inner')
    gdf['residentes_2018'] = j.groupby('index_right').pob.sum().reindex(gdf.index).fillna(0).round(0)
    anios_p = [f'pr_{a}' for a in range(int(sec['desde']), int(sec['hasta']) + 1)]
    if all(c in rep_manzanas.columns for c in anios_p):
        gdf['anios_persona'] = j.groupby('index_right')[anios_p].sum().sum(axis=1).reindex(gdf.index).fillna(0).round(0)
    gdf['periodo'] = f"{sec['desde']}-{sec['hasta']}"
    return gdf


def cols_rejilla(sec, k, sufijo=''):
    return {'n': f'Eventos de {sec["capa"]}{sufijo} en la hoja, {sec["desde"]}–{sec["hasta"]} (siempre ≥ {k})',
            'tam_m': 'Lado en metros de las celdas que forman la hoja (resolución alcanzada)',
            'fusion': '1 si la hoja une celdas hermanas que por separado no llegaban a k',
            'eventos_por_anio': 'Eventos por año en el periodo', 'area_km2': 'Área de la hoja (km²)',
            'densidad_km2': 'Eventos por km² en el periodo',
            'residentes_2018': 'Residentes 2018 (pr_total 2018) de las manzanas cuyo punto representativo cae en la hoja',
            'anios_persona': 'Años-persona del periodo (suma de pr_total de cada año) de esas mismas manzanas',
            'periodo': 'Años sumados'}


# ------------------------------------------------------------------------------------------- esquinas
def esquinas(nombre, sec, g, base, rep_manzanas, salida, pts_fuente):
    """Esquinas con al menos k eventos, y el resto de los eventos en una rejilla adaptativa propia.

    Los dos productos no comparten eventos: cada siniestro queda en una esquina publicada, en una hoja del resto
    o suprimido. Así no hay resta posible entre ellos.
    """
    k = g['k']
    subc = [p.split(':') for p in sec.get('subconteos', '').split(',') if p]
    pts = puntos(base, sec['capa'], sec['campo_anio'], int(sec['desde']), int(sec['hasta']), [c for c, _ in subc])
    pts_fuente[nombre] = pts
    tol = float(sec.get('tolerancia_m', 1))
    clave = ((pts.geometry.x / tol).round().astype(np.int64) * 100_000_000
             + (pts.geometry.y / tol).round().astype(np.int64))
    pts['esq'] = pd.factorize(clave.to_numpy())[0]
    tab = pts.groupby('esq').agg(n=('esq', 'size'), x=('geometry', lambda s: s.x.mean()),
                                 y=('geometry', lambda s: s.y.mean()))
    t = tab[tab.n >= k].copy()
    for col, alias in subc:
        sc = pts[pts[col] > 0].groupby('esq').size().reindex(t.index).fillna(0).astype(int)
        ok = ((sc >= k) | (sc == 0)) & (((t.n - sc) >= k) | ((t.n - sc) == 0))
        t[alias] = sc.where(ok)
    gdf = gpd.GeoDataFrame(t.drop(columns=['x', 'y']).reset_index(drop=True),
                           geometry=gpd.points_from_xy(t.x, t.y), crs=SRS)
    gdf['periodo'] = f"{sec['desde']}-{sec['hasta']}"
    salida[nombre] = gdf
    cols = {'n': f'Eventos en la esquina, {sec["desde"]}–{sec["hasta"]} (siempre ≥ {k})',
            'periodo': 'Años sumados'}
    for col, alias in subc:
        cols[alias] = (f'Eventos con {col} > 0; vacío si este conteo o su complemento quedan entre 1 y {k - 1}'
                       + (f'. {sec["nota"]}' if 'nota' in sec and alias in sec['nota'] else ''))
    describir(nombre, cols, sec['capa'], 'esquina')

    resto = pts[~pts.esq.isin(t.index)].reset_index(drop=True)
    nombre_resto = sec['rejilla_resto']
    hojas, suprimidos = particion(resto.geometry.x.to_numpy(), resto.geometry.y.to_numpy(), g['tamanos'], k)
    rg = hojas_a_gdf(hojas, sec, rep_manzanas)
    assert rg.n.sum() + suprimidos == len(resto)
    salida[nombre_resto] = rg
    describir(nombre_resto, cols_rejilla(sec, k, ' que no están en una esquina publicada'), sec['capa'],
              'rejilla adaptativa')
    publicados = gdf.n.sum() + rg.n.sum()
    registrar(nombre, 'esquinas', dict(sec), len(pts), gdf.n.sum(), k)
    registrar(nombre_resto, 'rejilla del resto', {**dict(sec), 'tamanos_m': g['tamanos']}, len(resto), rg.n.sum(), k)
    print(f'{nombre}: {len(tab)} esquinas, {len(gdf)} publicadas con {100 * gdf.n.sum() / len(pts):.1f} % de los '
          f'eventos; el resto en {len(rg)} hojas; en total {100 * publicados / len(pts):.1f} % publicado; '
          f'resto por resolución ' + resumen_resolucion(rg, len(resto)))


# ------------------------------------------------------------------------------------------- tasas
def tasa(nombre, sec, g, base, llaves, geometrias, salida, tasas_pub):
    k, nivel, padre = g['k'], sec['nivel'], sec['padre']
    con = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    ev = pd.read_sql(f'SELECT COD_DANE_A FROM "{sec["capa"]}" WHERE "{sec["campo_anio"]}" BETWEEN ? AND ?',
                     con, params=(int(sec['desde']), int(sec['hasta'])))
    anios = int(sec['hasta']) - int(sec['desde']) + 1
    ev = ev.merge(llaves[['COD_DANE_A', nivel]], on='COD_DANE_A', how='left')
    anios_p = [f'pr_{a}' for a in range(int(sec['desde']), int(sec['hasta']) + 1)]
    faltan = [c for c in anios_p if c not in llaves.columns]
    if faltan:
        sys.exit(f'{nombre}: la población de referencia no tiene los años {faltan}')
    L = llaves.dropna(subset=[nivel])
    if nivel == 'seccion':   # universo de la v2.5: solo las 945 secciones analíticas
        L = L[L.seccion.isin(llaves.attrs['secciones_analiticas'])]
    ev.loc[~ev[nivel].isin(L[nivel].unique()), nivel] = np.nan
    sin_unidad = int(ev[nivel].isna().sum())
    L = L.assign(anios_persona=L[anios_p].sum(axis=1))
    u = L.groupby(nivel).agg(
        residentes_2018=('pob', 'sum'), anios_persona=('anios_persona', 'sum'),
        padre=(padre, lambda s: s.mode().iat[0] if s.notna().any() else -1),
        nombre=('barrio', lambda s: s.mode().iat[0] if s.notna().any() else None))
    u['n'] = ev.groupby(nivel).size().reindex(u.index).fillna(0).astype(int)
    E = u.anios_persona
    con_res = E > 0
    m = u.n[con_res].sum() / E[con_res].sum()
    r = u.n / E.where(con_res)
    s2 = (E[con_res] * (r[con_res] - m) ** 2).sum() / E[con_res].sum()
    A = max(s2 - m / E[con_res].mean(), m * 1e-6)
    alfa, beta = m * m / A, m / A
    a_post, b_post = alfa + u.n, beta + E
    ci = float(g['nivel_confianza'])
    u['tasa_eb'] = (a_post / b_post * 1e5).where(con_res)
    ppf = lambda q: pd.Series(stats.gamma.ppf(q, a_post, scale=1 / b_post) * 1e5, index=u.index)
    u['ic_inf'] = ppf((1 - ci) / 2).where(con_res)
    u['ic_sup'] = ppf(1 - (1 - ci) / 2).where(con_res)
    u['tasa_cruda'] = (u.n / E * 1e5).where(con_res)
    u['precision_rel'] = ((u.ic_sup - u.ic_inf) / u.tasa_eb).round(2)
    u['estado'] = suprimir(u, 'padre', 'n', k)
    sup = u.estado != ''
    u.loc[sup, ['n', 'tasa_eb', 'ic_inf', 'ic_sup', 'tasa_cruda', 'precision_rel']] = np.nan
    n_complementaria = int((u.estado == 'complementaria').sum())
    # Una sola etiqueta para las dos supresiones: distinguirlas revelaría cuáles tienen al menos k eventos.
    u['estado'] = u.estado.map(lambda s: 'publicado' if s == '' else 'suprimido')
    umbral = float(sec.get('residentes_advertencia', 0))
    u['advertencia'] = np.select(
        [u.residentes_2018 <= 0, u.residentes_2018 < umbral],
        ['sin residentes: la tasa por residente no aplica',
         f'menos de {int(umbral)} residentes: la tasa por residente sobrestima el riesgo donde hay población flotante'],
        '')
    for c in ('tasa_eb', 'ic_inf', 'ic_sup', 'tasa_cruda'):
        u[c] = u[c].round(1)
    u['residentes_2018'] = u.residentes_2018.round(0)
    u['anios_persona'] = u.anios_persona.round(0)
    u = u.rename(columns={'padre': 'comuna'}).reset_index().rename(columns={nivel: 'unidad'})
    gdf = geometrias.merge(u, on='unidad', how='right')
    gdf = gpd.GeoDataFrame(gdf, geometry='geometry', crs=SRS)
    gdf['periodo'] = f"{sec['desde']}-{sec['hasta']}"
    salida[nombre] = gdf
    tasas_pub[nombre] = gdf.set_index('unidad').tasa_eb
    describir(nombre, {
        'unidad': f'Código de {nivel}', 'nombre': 'Nombre del barrio (moda de las manzanas)', 'comuna': 'Comuna',
        'residentes_2018': 'Residentes 2018 (pr_total 2018); el umbral de advertencia se mide con ellos',
        'anios_persona': f'Años-persona {sec["desde"]}–{sec["hasta"]}: suma de pr_total de cada año (denominador)',
        'n': f'Eventos {sec["desde"]}–{sec["hasta"]} (0 o ≥ {k}; vacío si suprimido)',
        'tasa_eb': f'Tasa por 100.000 años-persona, Bayes empírico Poisson-gamma global',
        'ic_inf': f'Límite inferior del intervalo de credibilidad al {int(ci * 100)} %',
        'ic_sup': f'Límite superior del intervalo de credibilidad al {int(ci * 100)} %',
        'tasa_cruda': 'Tasa cruda por 100.000 años-persona (inestable con pocos residentes)',
        'precision_rel': 'Ancho del intervalo dividido por la tasa suavizada: más alto, menos preciso',
        'estado': 'publicado | suprimido (menos de k, o complementaria para que no se deduzca por resta)',
        'advertencia': 'Aviso sobre el denominador; no suprime', 'periodo': 'Años sumados'},
        sec['capa'], nivel)
    total = len(ev) - sin_unidad
    registrar(nombre, 'tasa', {**dict(sec), 'm_ciudad_100k': round(m * 1e5, 2), 'A': A,
                               'suprimidas': int(sup.sum()), 'complementarias': n_complementaria}, total,
              u.n.sum(), k)
    print(f'{nombre}: {len(u)} unidades, {int(sup.sum())} suprimidas ({n_complementaria} por complementaria), '
          f'{int((u.advertencia != "").sum())} con advertencia de denominador; tasa ciudad {m * 1e5:.1f}')


# ------------------------------------------------------------------------------------------- conteos
def conteo(nombre, sec, g, base, salida):
    k = g['k']
    unidades = gpd.read_file(base, layer=sec['unidades'], engine='pyogrio')
    llave, dim = sec['llave_unidad'], sec['dimension']
    pts = gpd.read_file(base, layer=sec['capa'], columns=[dim], engine='pyogrio')
    pts = pts[pts.geometry.notna()]
    j = gpd.sjoin(pts, unidades[[llave, 'geometry']], predicate='within', how='left')
    j['d'] = j[dim].fillna('sin dato').map(slug)
    tab = j.dropna(subset=[llave]).groupby([llave, 'd']).size().unstack(fill_value=0)
    tab.columns = [f'n_{c}' for c in tab.columns]
    tab['n_total'] = tab.sum(axis=1)
    # total por barrio: primaria y complementaria frente al total de la ciudad
    largo = tab[['n_total']].assign(g=0)
    mot_total = suprimir(largo, 'g', 'n_total', k)
    dims = [c for c in tab.columns if c != 'n_total']
    for b in tab.index:
        if mot_total[b]:
            tab.loc[b, :] = np.nan
            continue
        fila = pd.DataFrame({'v': tab.loc[b, dims].astype(int), 'g': 0})
        mot = suprimir(fila, 'g', 'v', k)
        tab.loc[b, [c for c in dims if mot[c]]] = np.nan
    tab = tab.reset_index()
    gdf = unidades[[llave, 'BARRIO', 'geometry']].merge(tab, on=llave, how='inner')
    salida[nombre] = gpd.GeoDataFrame(gdf, geometry='geometry', crs=SRS)
    cols = {c: f'Registros de {sec["capa"]} en el barrio con {dim} = {c[2:]} (0 o ≥ {k}; vacío si suprimido)'
            for c in dims}
    cols['n_total'] = f'Registros de {sec["capa"]} en el barrio (0 o ≥ {k}; vacío si suprimido)'
    describir(nombre, cols, sec['capa'], 'barrio')
    publicados = tab.n_total.sum()
    registrar(nombre, 'conteo', dict(sec), int(j[llave].notna().sum()), publicados, k)
    print(f'{nombre}: {len(tab)} barrios con registros, {int(tab.n_total.isna().sum())} suprimidos; '
          f'{100 * publicados / max(j[llave].notna().sum(), 1):.1f} % de los registros publicados')


# ------------------------------------------------------------------------------------------- fronteras
def vias(base, tipos):
    v = gpd.read_file(base, layer='pot_jerarquia_vial', columns=['tipo_via'], engine='pyogrio')
    return v[v.tipo_via.isin(tipos)]


def fronteras(nombre, sec, g, base, llaves, manzana_vals, tasas_pub, salida):
    rng = np.random.default_rng(int(g['semilla']))
    U = gpd.read_file(base, layer=sec['unidades'], engine='pyogrio')
    llave = sec['llave_unidad']
    U = U[[llave, 'BARRIO', 'geometry']].reset_index(drop=True)
    vars_ = [v.strip() for v in sec['variables'].split(',')]
    vals = pd.DataFrame(index=U[llave])
    mz = llaves[['COD_DANE_A', sec['llave_manzana'], 'pob']].merge(manzana_vals, on='COD_DANE_A', how='left')
    for v in vars_:
        col = 'c_' + slug(v.replace('tasa:', 'tasa_'))
        if v.startswith('tasa:'):
            vals[col] = tasas_pub[v.split(':', 1)[1]].reindex(vals.index)
        else:
            ok = mz[v].notna() & (mz.pob > 0)
            w = mz[ok].groupby(sec['llave_manzana']).apply(lambda d: np.average(d[v], weights=d.pob),
                                                             include_groups=False)
            vals[col] = w.reindex(vals.index)
    sd = vals.std()
    buf = float(g['buffer_via_m'])
    art = unary_union(vias(base, g['vias_arteriales']).geometry).buffer(buf)
    colec = unary_union(vias(base, g['vias_colectoras']).geometry).buffer(buf)
    asent = unary_union(gpd.read_file(base, layer=sec['asentamientos'], engine='pyogrio').geometry).buffer(buf)
    sidx = U.sindex
    filas = []
    for i, gi in enumerate(U.geometry):
        for j in sidx.query(gi.buffer(10), predicate='intersects'):
            if j <= i:
                continue
            linea = gi.boundary.intersection(U.geometry[j].buffer(10))
            L = linea.length
            if L < float(sec['longitud_minima_m']):
                continue
            a, b = U[llave][i], U[llave][j]
            f = {'barrio_a': a, 'barrio_b': b, 'nombre_a': U.BARRIO[i], 'nombre_b': U.BARRIO[j],
                 'longitud_m': round(L, 1), 'prop_arterial': round(linea.intersection(art).length / L, 3),
                 'prop_colectora': round(linea.intersection(colec).length / L, 3),
                 'prop_asentamiento': round(linea.intersection(asent).length / L, 3), 'geometry': linea}
            for c in vals.columns:
                d = abs(vals.at[a, c] - vals.at[b, c]) / sd[c]
                f[c] = round(d, 3) if pd.notna(d) else np.nan
            filas.append(f)
    F = gpd.GeoDataFrame(filas, geometry='geometry', crs=SRS)
    salida[nombre] = F
    # Prueba: ¿el contraste crece con la proporción del borde que corre sobre una vía estructurante?
    perms = int(g['permutaciones'])
    prueba = []
    for c in vals.columns:
        d = F[[c, 'prop_arterial', 'prop_colectora', 'longitud_m']].dropna()
        zx = stats.zscore(stats.rankdata(d.prop_arterial))
        zy = stats.zscore(stats.rankdata(d[c]))
        rho = float(np.mean(zx * zy))                       # Spearman = Pearson de los rangos
        nulos = rng.permuted(np.tile(zy, (perms, 1)), axis=1) @ zx / len(zx)
        p = (1 + (np.abs(nulos) >= abs(rho)).sum()) / (perms + 1)
        sobre = d.prop_arterial >= 0.5
        prueba.append({'variable': c[2:], 'bordes': len(d), 'bordes_sobre_arteria': int(sobre.sum()),
                       'rho_spearman': round(rho, 3), 'p_permutacion': round(p, 4),
                       'contraste_medio_sobre_arteria': round(d[c][sobre].mean(), 3),
                       'contraste_medio_sin_arteria': round(d[c][~sobre].mean(), 3),
                       'razon': round(d[c][sobre].mean() / d[c][~sobre].mean(), 2)})
    P = pd.DataFrame(prueba)
    salida[nombre + '_prueba'] = P
    cols = {'barrio_a': 'Código del barrio a un lado del borde', 'barrio_b': 'Código del barrio al otro lado',
            'nombre_a': 'Nombre del barrio a', 'nombre_b': 'Nombre del barrio b',
            'longitud_m': 'Longitud del borde compartido (m)',
            'prop_arterial': f'Proporción del borde a ≤{int(buf)} m de una arteria o vía interregional',
            'prop_colectora': f'Proporción del borde a ≤{int(buf)} m de una vía colectora',
            'prop_asentamiento': f'Proporción del borde a ≤{int(buf)} m de un asentamiento de desarrollo incompleto'}
    for v in vars_:
        c = 'c_' + slug(v.replace('tasa:', 'tasa_'))
        cols[c] = f'Contraste entre los dos lados en {v}: diferencia absoluta en desviaciones estándar entre barrios'
    describir(nombre, cols, 'barrios', 'borde entre barrios')
    describir(nombre + '_prueba', {
        'variable': 'Variable contrastada', 'bordes': 'Bordes con dato en ambos lados',
        'bordes_sobre_arteria': 'Bordes con la mitad o más de su longitud sobre una vía estructurante',
        'rho_spearman': 'Correlación de rangos entre contraste y proporción arterial del borde',
        'p_permutacion': f'Valor p bilateral por {perms} permutaciones del contraste entre bordes',
        'contraste_medio_sobre_arteria': 'Contraste medio de los bordes sobre vía estructurante',
        'contraste_medio_sin_arteria': 'Contraste medio de los demás bordes',
        'razon': 'Cociente entre los dos contrastes medios'}, 'fronteras', 'prueba')
    registrar(nombre, 'fronteras', dict(sec), len(F), len(F), g['k'])
    print(f'{nombre}: {len(F)} bordes entre {U.shape[0]} barrios; '
          + '; '.join(f'{r.variable} rho={r.rho_spearman} p={r.p_permutacion} razón={r.razon}'
                      for r in P.itertuples()))


# ------------------------------------------------------------------------------------------- fronteras de manzana
def fronteras_manzana(nombre, sec, g, base, llaves, vals, salida):
    """Pares de manzanas que se miran de frente a través de una calle: ¿separa más una arteria que una local?

    Un par son dos manzanas a menos de distancia_max_m cuya línea más corta no atraviesa una tercera manzana.
    El par es «arterial» si esa línea cruza el eje de una vía estructurante, «colectora» si cruza una colectora y
    «local» en otro caso. El contraste se compara dentro de estratos de comuna y distancia (las arterias son más
    anchas y la distancia por sí sola aumenta el contraste), con permutación de etiquetas dentro de cada estrato.
    Solo usa variables públicas de manzana.
    """
    import shapely
    rng = np.random.default_rng(int(g['semilla']))
    dmax = float(sec['distancia_max_m'])
    bandas = [float(b) for b in sec['bandas_m'].split(',')]
    vars_ = [v.strip() for v in sec['variables'].split(',')]
    mz = gpd.read_file(base, layer='manzanas', columns=['COD_DANE_A'], engine='pyogrio')
    mz = mz.merge(llaves[['COD_DANE_A', 'comuna_n']], on='COD_DANE_A', how='left').merge(vals, on='COD_DANE_A',
                                                                                         how='left')
    geoms = mz.geometry.values
    arbol = shapely.STRtree(geoms)
    i, j = arbol.query(geoms, predicate='dwithin', distance=dmax)
    sel = i < j
    i, j = i[sel], j[sel]
    lineas = shapely.shortest_line(geoms[i], geoms[j])
    # descartar pares cuya línea atraviesa el interior de una tercera manzana
    encogidas = shapely.buffer(geoms, -1.0)
    li, pi = shapely.STRtree(encogidas).query(lineas, predicate='intersects')
    tapadas = np.zeros(len(lineas), bool)
    tapadas[li[(pi != i[li]) & (pi != j[li])]] = True
    i, j, lineas = i[~tapadas], j[~tapadas], lineas[~tapadas]
    dist = shapely.length(lineas)
    vias_g = gpd.read_file(base, layer='pot_jerarquia_vial', columns=['tipo_via'], engine='pyogrio').reset_index(drop=True)
    tipo = np.full(len(lineas), 'local', dtype=object)
    tramo = np.full(len(lineas), -1)
    for clase, tipos in (('colectora', g['vias_colectoras']), ('arterial', g['vias_arteriales'])):
        sub = vias_g[vias_g.tipo_via.isin(tipos)]
        a, b = shapely.STRtree(sub.geometry.values).query(lineas, predicate='intersects')
        tipo[a] = clase
        if clase == 'arterial':
            tramo[a] = sub.index.values[b]
    P = pd.DataFrame({'i': i, 'j': j, 'cod_i': mz.COD_DANE_A.values[i], 'cod_j': mz.COD_DANE_A.values[j],
                      'dist': dist, 'tipo': tipo, 'tramo': tramo, 'comuna': mz.comuna_n.values[i]})
    P['banda'] = np.digitize(P.dist, bandas)
    P['estrato'] = P.comuna.fillna(-1).astype(int).astype(str) + '|' + P.banda.astype(str)
    columnas = {}
    for v in vars_:
        x = mz[v].to_numpy(dtype=float)
        sd = np.nanstd(x)
        c = 'c_' + slug(v)
        P[c] = np.abs(x[P.i] - x[P.j]) / sd
        columnas[c] = v
    prueba = []
    perms = int(g['permutaciones'])
    for c, v in columnas.items():
        d = P[P[c].notna() & P.tipo.isin(['arterial', 'local'])]
        y = d[c].to_numpy()
        lab = (d.tipo == 'arterial').to_numpy()
        est = pd.factorize(d.estrato)[0]
        # diferencia de medias arterial − local dentro de cada estrato, promediada con peso = pares arteriales
        S = est.max() + 1

        def estadistico(a):
            n1 = np.bincount(est, weights=a, minlength=S)
            n0 = np.bincount(est, weights=~a, minlength=S)
            m1 = np.bincount(est, weights=y * a, minlength=S) / np.where(n1 > 0, n1, 1)
            m0 = np.bincount(est, weights=y * ~a, minlength=S) / np.where(n0 > 0, n0, 1)
            ok = (n1 > 0) & (n0 > 0) & (m0 > 0)
            return np.average((m1 - m0)[ok], weights=n1[ok]), np.average((m1 / np.where(ok, m0, 1))[ok], weights=n1[ok])

        obs, razon = estadistico(lab)
        pos = np.argsort(est, kind='stable')
        nulos = np.empty(perms)
        for t_ in range(perms):
            barajado = np.argsort(est + rng.random(len(est)))   # mismos bloques por estrato, orden al azar
            lp = np.empty_like(lab)
            lp[pos] = lab[barajado]
            nulos[t_] = estadistico(lp)[0]
        p = (1 + (np.abs(nulos) >= abs(obs)).sum()) / (perms + 1)
        prueba.append({'variable': v, 'pares_arteriales': int(lab.sum()), 'pares_locales': int((~lab).sum()),
                       'diferencia_media': round(obs, 3), 'razon_media': round(razon, 2), 'p_permutacion': round(p, 4)})
    T = pd.DataFrame(prueba)
    salida[nombre + '_prueba'] = T
    # Qué tramos arteriales funcionan como borde: contraste medio de los pares que separan frente a los pares
    # locales del mismo estrato.
    base_local = P[P.tipo == 'local'].groupby('estrato')[list(columnas)].mean()
    A = P[P.tipo == 'arterial'].copy()
    rel = A[list(columnas)].values / base_local.reindex(A.estrato).values
    for k_, c in enumerate(columnas):
        A['r_' + c[2:]] = rel[:, k_]
    agg = A.groupby('tramo').agg(n_pares=('i', 'size'), **{c: (c, 'mean') for c in columnas},
                                 **{'r_' + c[2:]: ('r_' + c[2:], 'mean') for c in columnas})
    agg = agg[agg.n_pares >= int(sec['pares_minimos_tramo'])]
    nombre_b = llaves.set_index('COD_DANE_A').barrio.reindex(mz.COD_DANE_A).to_numpy()
    lados = pd.concat([pd.DataFrame({'tramo': A.tramo, 'b': nombre_b[A.i]}),
                       pd.DataFrame({'tramo': A.tramo, 'b': nombre_b[A.j]})]).dropna()
    agg['barrios'] = lados.groupby('tramo').b.agg(lambda s: ' · '.join(s.value_counts().index[:4])) \
        .reindex(agg.index)
    tramos = vias_g.loc[agg.index, ['tipo_via', 'geometry']].join(agg)
    tramos.insert(0, 'tramo_id', agg.index.astype(int))
    for c in tramos.columns:
        if c.startswith(('c_', 'r_')):
            tramos[c] = tramos[c].round(3)
    salida[nombre + '_tramos'] = gpd.GeoDataFrame(tramos.reset_index(drop=True), geometry='geometry', crs=SRS)
    # Pares de manzanas: insumo para los módulos de manzana de la base central; nunca se escribe en la zona pública.
    salida['_' + nombre + '_pares'] = P
    describir(nombre + '_prueba', {
        'variable': 'Variable de manzana contrastada', 'pares_arteriales': 'Pares de manzanas separados por una vía estructurante',
        'pares_locales': 'Pares separados por una vía local',
        'diferencia_media': 'Contraste medio arterial menos local dentro de comuna y banda de distancia (desviaciones estándar)',
        'razon_media': 'Cociente medio del contraste arterial sobre el local dentro de cada estrato',
        'p_permutacion': f'Valor p bilateral por {perms} permutaciones de la etiqueta dentro de cada estrato'},
        'manzanas', 'pares de manzanas')
    cols_t = {'tramo_id': 'Posición del tramo en pot_jerarquia_vial (capa congelada de la base de referencia)',
              'tipo_via': 'Clase de la vía (POT)', 'n_pares': 'Pares de manzanas que el tramo separa',
              'barrios': 'Barrios más frecuentes entre las manzanas que el tramo separa (hasta cuatro)'}
    for c, v in columnas.items():
        cols_t[c] = f'Contraste medio en {v} entre las manzanas que el tramo separa (desviaciones estándar)'
        cols_t['r_' + c[2:]] = f'Ese contraste dividido por el de los pares locales de la misma comuna y distancia (1 = como una calle local)'
    describir(nombre + '_tramos', cols_t, 'pot_jerarquia_vial', 'tramo vial')
    registrar(nombre, 'fronteras de manzana', dict(sec), len(P), len(P), g['k'])
    print(f'{nombre}: {len(P)} pares ({(P.tipo == "arterial").sum()} arteriales, {(P.tipo == "colectora").sum()} '
          f'colectoras); {len(tramos)} tramos con al menos {sec["pares_minimos_tramo"]} pares; '
          + '; '.join(f'{r.variable} razón={r.razon_media} p={r.p_permutacion}' for r in T.itertuples()))


def en_baldosas(zona, lado=1000.0):
    """Parte un polígono enorme (la franja alrededor de todas las arterias) en baldosas, para que las uniones
    espaciales usen el índice en lugar de comparar cada geometría contra la franja completa."""
    x0, y0, x1, y1 = zona.bounds
    xs = np.arange(np.floor(x0 / lado) * lado, x1 + lado, lado)
    ys = np.arange(np.floor(y0 / lado) * lado, y1 + lado, lado)
    tiles = [box(a, b, a + lado, b + lado) for a in xs for b in ys]
    t = gpd.GeoDataFrame(geometry=tiles, crs=SRS)
    t = t[t.intersects(zona)]
    t['geometry'] = t.intersection(zona)
    return t[~t.is_empty].reset_index(drop=True)


def largo_dentro(lineas, piezas):
    """Longitud de cada línea dentro de la zona dada en piezas."""
    l = gpd.GeoDataFrame({'k': np.arange(len(lineas))}, geometry=lineas.values, crs=SRS)
    o = gpd.overlay(l, piezas, how='intersection', keep_geom_type=False)
    return o.geometry.length.groupby(o.k).sum().reindex(np.arange(len(lineas))).fillna(0).to_numpy()


def dentro(puntos_, piezas):
    j = gpd.sjoin(puntos_[['geometry']], piezas[['geometry']], predicate='within', how='inner')
    return pd.Series(puntos_.index.isin(j.index), index=puntos_.index)


# ------------------------------------------------------------------------------------------- corredores
def corredores(nombre, sec, g, base, fuentes_pts, salida):
    k, buf = g['k'], float(g['buffer_via_m'])
    red = gpd.read_file(base, layer='pot_jerarquia_vial', columns=['tipo_via', 'zona'], engine='pyogrio')
    red = red[red.zona == 'URBANA'].reset_index(drop=True)
    com = gpd.read_file(base, layer='comunas_corregimientos', columns=['unidad_admin'], engine='pyogrio')
    zonas = {'arterial': en_baldosas(unary_union(red[red.tipo_via.isin(g['vias_arteriales'])].geometry).buffer(buf)),
             'colectora': en_baldosas(unary_union(red[red.tipo_via.isin(g['vias_colectoras'])].geometry).buffer(buf))}
    # Exposición: cuánto de cada tramo de la red cae en cada franja; el tramo se asigna a la comuna de su punto medio.
    red['L'] = red.length
    for zn, zg in zonas.items():
        red[f'L_{zn}'] = largo_dentro(red.geometry, zg)
    medio = gpd.GeoDataFrame(red.drop(columns='geometry'), geometry=red.interpolate(0.5, normalized=True), crs=SRS)
    red['unidad_admin'] = gpd.sjoin(medio, com, predicate='within', how='left') \
        .groupby(level=0).unidad_admin.first()
    filas = []
    for fuente in [f.strip() for f in sec['fuentes'].split(',')]:
        pts = gpd.sjoin(fuentes_pts[fuente][['geometry']], com, predicate='within', how='left')
        pts = pts[~pts.index.duplicated()]
        for zn, zg in zonas.items():
            pts[zn] = dentro(pts, zg)
        ambitos = [('ciudad', None, pts, red)]
        for c, sub in pts.groupby('unidad_admin'):
            ambitos.append(('unidad_admin', c, sub, red[red.unidad_admin == c]))
        for tipo, cod, sub, r in ambitos:
            L = r.L.sum()
            if L == 0:
                continue
            f = {'fuente': fuente, 'ambito': tipo, 'codigo': cod, 'n': len(sub)}
            for zn in zonas:
                ne = int(sub[zn].sum())
                f[f'n_{zn}'] = ne
                f[f'prop_eventos_{zn}'] = ne / len(sub)
                f[f'prop_red_{zn}'] = r[f'L_{zn}'].sum() / L
                f[f'razon_{zn}'] = f[f'prop_eventos_{zn}'] / f[f'prop_red_{zn}'] if f[f'prop_red_{zn}'] > 0 else np.nan
            filas.append(f)
    T = pd.DataFrame(filas)
    # Todo conteo (y su complemento) 0 o ≥ k; si no, la fila se vacía salvo identificadores.
    for zn in zonas:
        malo = ((T[f'n_{zn}'] > 0) & (T[f'n_{zn}'] < k)) | (((T.n - T[f'n_{zn}']) > 0) & ((T.n - T[f'n_{zn}']) < k))
        T.loc[malo, [f'n_{zn}', f'prop_eventos_{zn}', f'razon_{zn}']] = np.nan
    T.loc[T.n < k, [c for c in T.columns if c not in ('fuente', 'ambito', 'codigo')]] = np.nan
    for c in T.columns:
        if c.startswith(('prop_', 'razon_')):
            T[c] = T[c].astype(float).round(3)
    T['codigo'] = T.codigo.astype(str).replace('None', '')
    salida[nombre] = T
    cols = {'fuente': 'Producto de origen (rejilla)', 'ambito': 'ciudad | unidad_admin (comuna o corregimiento)',
            'codigo': 'Comuna o corregimiento',
            'n': f'Eventos en el ámbito (0 o ≥ {k})'}
    for zn in zonas:
        cols[f'n_{zn}'] = f'Eventos a ≤{int(buf)} m de una vía {zn} (0 o ≥ {k}, con su complemento)'
        cols[f'prop_eventos_{zn}'] = f'Proporción de eventos a ≤{int(buf)} m de una vía {zn}'
        cols[f'prop_red_{zn}'] = f'Proporción de la red vial urbana a ≤{int(buf)} m de una vía {zn}'
        cols[f'razon_{zn}'] = 'Concentración: proporción de eventos dividida por proporción de red (1 = sin concentración)'
    describir(nombre, cols, 'rejillas', 'ciudad y comuna')
    registrar(nombre, 'corredores', dict(sec), 0, 0, k)
    ciudad = T[T.ambito == 'ciudad']
    print(f'{nombre}: ' + '; '.join(f'{r.fuente} arterial {r.prop_eventos_arterial:.0%} de eventos sobre '
                                    f'{r.prop_red_arterial:.0%} de red (razón {r.razon_arterial:.2f})'
                                    for r in ciudad.itertuples()))


# ------------------------------------------------------------------------------------------- principal
def main(base, conf_dir, destino, devolver=False):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(os.path.join(conf_dir, 'transformaciones.conf'), encoding='utf-8')
    G = cp['general']
    g = {'k': int(G['k']), 'tamanos': [int(t) for t in G['tamanos_m'].split(',')],
         'nivel_confianza': G['nivel_confianza'], 'buffer_via_m': G['buffer_via_m'],
         'vias_arteriales': [v.strip() for v in G['vias_arteriales'].split(',')],
         'vias_colectoras': [v.strip() for v in G['vias_colectoras'].split(',')],
         'permutaciones': G['permutaciones'], 'semilla': G['semilla']}
    t = g['tamanos']
    if any(t[i] != 2 * t[i + 1] for i in range(len(t) - 1)):
        sys.exit('tamanos_m: cada lado debe ser el doble del siguiente')

    con = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    # Llaves de la v2.5: comuna oficial (ter_comuna, no la etiqueta censal) y población de referencia por año.
    llaves = pd.read_sql('SELECT l.COD_DANE_A, l.seccion, l.id_barrio, l.barrio, t.ter_comuna AS comuna_n '
                         'FROM sc_llaves l LEFT JOIN m_territorio_oficial t USING (COD_DANE_A)', con)
    pr = pd.read_sql('SELECT COD_DANE_A, vigencia, pr_total FROM m_poblacion_referencia_v25', con)
    pr = pr.pivot_table(index='COD_DANE_A', columns='vigencia', values='pr_total', aggfunc='sum')
    pr.columns = [f'pr_{int(c)}' for c in pr.columns]
    llaves = llaves.merge(pr.reset_index(), on='COD_DANE_A', how='left')
    llaves['pob'] = llaves['pr_2018'].fillna(0)
    llaves.attrs['secciones_analiticas'] = set(pd.read_sql(
        'SELECT seccion FROM m_seccion_marco WHERE scm_analitica = 1', con).seccion)
    mz = gpd.read_file(base, layer='manzanas', columns=['COD_DANE_A'], engine='pyogrio')
    rep = gpd.GeoDataFrame(mz[['COD_DANE_A']].merge(llaves[['COD_DANE_A', 'pob'] + [c for c in llaves.columns
                                                                                    if c.startswith('pr_')]],
                                                     on='COD_DANE_A'),
                           geometry=mz.representative_point().values, crs=SRS)
    secciones_geom = mz.merge(llaves[['COD_DANE_A', 'seccion']], on='COD_DANE_A').dissolve('seccion') \
        .reset_index()[['seccion', 'geometry']].rename(columns={'seccion': 'unidad'})
    barrios_geom = gpd.read_file(base, layer='barrios', columns=['ID_BARRIO'], engine='pyogrio') \
        .rename(columns={'ID_BARRIO': 'unidad'})
    geoms = {'seccion': secciones_geom, 'id_barrio': barrios_geom}
    # Mecanismos de deterioro de la v2.5 (dt3_, censo 2018 sobre el universo único), no los det_ congelados de la v2.3.
    vals = pd.read_sql('SELECT COD_DANE_A, ' + ', '.join(c for c in (
        'dt3_inquilinato_pct', 'dt3_envejecimiento_pct', 'dt3_densificacion_pct', 'dt3_precariedad_pct')) +
        ' FROM m_deterioro_v3 WHERE vigencia = 2018', con)
    vals = pd.read_sql('SELECT COD_DANE_A FROM manzanas', con).merge(vals, on='COD_DANE_A', how='left')
    vals = vals.merge(pd.read_sql('SELECT COD_DANE_A, fys5_idx_social, fys5_idx_fisico FROM m_fisico_social_v25 '
                                  'WHERE vigencia = 2025', con), on='COD_DANE_A', how='left')
    vals = vals.merge(pd.read_sql('SELECT COD_DANE_A, edh_anios_prom_25 AS "edh_anios_prom_25@2018" '
                                  'FROM m_educacion_hogar WHERE vigencia = 2018', con), on='COD_DANE_A', how='left')

    salida, pts_fuente, tasas_pub = {}, {}, {}
    secciones = cp.sections()
    for s in [x for x in secciones if x.startswith('rejilla:')]:
        nombre = s.split(':', 1)[1]
        pts_fuente[nombre] = rejilla(nombre, cp[s], g, base, rep, salida)
    for s in [x for x in secciones if x.startswith('esquinas:')]:
        esquinas(s.split(':', 1)[1], cp[s], g, base, rep, salida, pts_fuente)
    for s in [x for x in secciones if x.startswith('tasa:')]:
        sec = cp[s]
        tasa(s.split(':', 1)[1], sec, g, base, llaves, geoms[sec['nivel']], salida, tasas_pub)
    for s in [x for x in secciones if x.startswith('conteo:')]:
        conteo(s.split(':', 1)[1], cp[s], g, base, salida)
    for s in [x for x in secciones if x.startswith('fronteras:')]:
        fronteras(s.split(':', 1)[1], cp[s], g, base, llaves, vals, tasas_pub, salida)
    for s in [x for x in secciones if x.startswith('fronteras_manzana:')]:
        fronteras_manzana(s.split(':', 1)[1], cp[s], g, base, llaves, vals, salida)
    for s in [x for x in secciones if x.startswith('corredores:')]:
        corredores(s.split(':', 1)[1], cp[s], g, base, pts_fuente, salida)

    if devolver:
        return salida, g, llaves
    tmp = tempfile.mkdtemp(prefix='.transformados.', dir=destino)
    try:
        ruta = os.path.join(tmp, 'transformados.gpkg')
        for nombre, df in salida.items():
            if nombre.startswith('_'):
                continue
            if isinstance(df, gpd.GeoDataFrame):
                df.to_file(ruta, layer=nombre, driver='GPKG', engine='pyogrio')
            else:
                pyogrio.write_dataframe(pd.DataFrame(df), ruta, layer=nombre, driver='GPKG')
        reg = pd.DataFrame(REGISTRO)
        pyogrio.write_dataframe(reg, ruta, layer='transformaciones_registro', driver='GPKG')
        describir('transformaciones_registro', {
            'objeto': 'Producto', 'tipo': 'Tipo de transformación', 'k': 'Umbral de eventos por unidad publicada',
            'eventos_total': 'Eventos de origen en el periodo', 'eventos_publicados': 'Eventos dentro de unidades publicadas',
            'eventos_suprimidos': 'Eventos en unidades suprimidas', 'pct_publicado': 'Porcentaje publicado',
            'parametros': 'Parámetros de la transformación (JSON)'}, 'transformaciones', 'registro')
        with open(os.path.join(tmp, 'transformados.campos.csv'), 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(DICC[0]))
            w.writeheader()
            w.writerows(DICC)
        for archivo in os.listdir(tmp):
            os.replace(os.path.join(tmp, archivo), os.path.join(destino, archivo))
            os.chmod(os.path.join(destino, archivo), 0o644)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
