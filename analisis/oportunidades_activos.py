#!/usr/bin/env python3
"""Índices de estructura de oportunidades y de activos del hogar por manzana, solo con fuentes públicas.

Marco: Kaztman y Filgueira (CEPAL, 1999). La vulnerabilidad es el desajuste entre los activos que controla el
hogar y la estructura de oportunidades que ofrecen el Estado, el mercado y la sociedad. Construcción según el
Handbook OCDE-JRC (2008) y el Child Opportunity Index 3.0: recorte de extremos, estandarización, orientación,
dominios con igual peso, agregación aritmética con alternativas geométrica y de componente principal, y una
clase de robustez por manzana. Conteo de privaciones al estilo Alkire-Foster (el método del IPM de Colombia).

Universo: 13.280 manzanas residenciales de cabecera (las que tienen tipología espacial). Pesos: residentes 2018.
Uso: oportunidades_activos.py BASE.gpkg SALIDA_CSV   (escribe también SALIDA.tipologias.csv)
"""
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy import stats

OPORTUNIDADES = {   # dominio: [(campo, signo)]  signo -1 = menos es mejor (minutos)
    'educacion': [('ofe_cupos_prim_por_nino', 1), ('ofe_cupos_sec_por_joven', 1), ('ofe_pct_cupos_alto_desempeno', 1),
                  ('esp_min_colegio_oficial', -1), ('esp_min_ed_superior_tecnica', -1)],
    'salud': [('ofe_salud_prim_por_10mil', 1), ('esp_min_salud_primaria_publica', -1), ('esp_min_hospital', -1)],
    'trabajo_ciudad': [('acf_opp45_pico_am', 1), ('acf_opp45_noche', 1), ('acc_ai', 1)],
    'espacio_publico': [('esp_min_parque', -1), ('esp_min_deporte', -1), ('esp_n_deporte_15min', 1)],
    'vida_barrio': [('catr_mezcla_usos', 1), ('esp_inters_km2', 1)],
}
ACTIVOS = {
    'capital_humano': [('mc_pct_edu_sup25', 1), ('mc_pct_edu_baja25', -1), ('edh_anios_prom_25', 1),
                       ('mc_pct_inasist_5_14', -1)],
    'trabajo': [('eb18_desocup', -1), ('eb18_uso_mixto', 1)],
    'vivienda_conectividad': [('eb18_hac_critico', -1), ('eb18_pared_prec', -1), ('pct_internet', 1)],
}
COMPOSICION = ['mc_pct_unip', 'mc_pct_unip_60', 'mc_pct_jef_fem', 'mc_pct_hog_nopar', 'eb18_hog_extra', 'pct_0_14',
               'pct_60']
UMBRAL_PRIVACION = 0.20   # un dominio está privado si su puntaje cae en el 20 % inferior (ponderado por residentes)
K_PRIVACIONES = 2         # privación múltiple de oportunidades: 2 o más dominios privados


def wquant(x, w, q):
    o = np.argsort(x)
    cw = np.cumsum(w[o]) / w.sum()
    return x[o][np.searchsorted(cw, q)]


def zw(s, w):
    v = s.to_numpy(float)
    ok = ~np.isnan(v)
    lo, hi = np.nanpercentile(v, [1, 99])
    v = np.clip(v, lo, hi)
    m = np.average(v[ok], weights=w[ok])
    sd = np.sqrt(np.average((v[ok] - m) ** 2, weights=w[ok]))
    return pd.Series((v - m) / sd, index=s.index)


def pct_rank_w(s, w):
    v = s.to_numpy(float)
    out = np.full(len(v), np.nan)
    ok = ~np.isnan(v)
    o = np.argsort(v[ok])
    cw = np.cumsum(w[ok][o]) / w[ok].sum()
    r = np.empty(ok.sum())
    r[o] = cw
    out[ok] = r
    return pd.Series(out * 100, index=s.index)


def indice(df, dominios, w, prefijo):
    """Devuelve dominios (z medio de indicadores), índice aritmético, geométrico y de componente principal."""
    D = pd.DataFrame(index=df.index)
    for dom, inds in dominios.items():
        Z = pd.concat([s * zw(df[c], w) for c, s in inds], axis=1)
        disponibles = Z.notna().sum(axis=1)
        D[dom] = Z.mean(axis=1).where(disponibles >= np.ceil(len(inds) / 2))
    completo = D.notna().all(axis=1)
    arit = D.mean(axis=1).where(completo)
    # geométrica sobre percentiles ponderados de cada dominio (menos compensatoria)
    P = pd.concat([pct_rank_w(D[c], w) for c in D.columns], axis=1).clip(lower=1) / 100
    geom = np.exp(np.log(P).mean(axis=1)).where(completo) * 100
    # componente principal sobre los dominios estandarizados
    Dz = D[completo]
    Dz = (Dz - Dz.mean()) / Dz.std()
    vals, vecs = np.linalg.eigh(np.cov(Dz.T))
    v1 = vecs[:, -1] * np.sign(vecs[:, -1].sum())
    acp = pd.Series(Dz.to_numpy() @ v1, index=Dz.index).reindex(df.index)
    var_exp = vals[-1] / vals.sum()
    return D, arit, geom, acp, var_exp, v1


def quintil(s, w):
    p = pct_rank_w(s, w)
    return np.ceil(p / 20).clip(1, 5)


def main(base, salida):
    con = sqlite3.connect(f'file:{base}?mode=ro', uri=True)
    campos_m = ['mc_pct_edu_sup25', 'mc_pct_edu_baja25', 'mc_pct_inasist_5_14', 'eb18_desocup', 'eb18_uso_mixto',
                'eb18_hac_critico', 'eb18_pared_prec', 'TP19_INTE1', 'TP19_INTE2', 'mc_pct_unip', 'mc_pct_unip_60',
                'mc_pct_jef_fem', 'mc_pct_hog_nopar', 'eb18_hog_extra', 'mc_n_e0_14', 'mc_n_e60', 'mc_personas',
                'mc_hogares', 'pob18_v196', 'estrato_moda']
    df = pd.read_sql('SELECT COD_DANE_A, ' + ', '.join(f'"{c}"' for c in campos_m) + ' FROM manzanas', con)
    for t, cols in (('m_espacial_manzana_2025', ['esp_tipo_espacial', 'esp_min_colegio_oficial', 'esp_min_ed_superior_tecnica',
                                                 'esp_min_salud_primaria_publica', 'esp_min_hospital', 'esp_min_parque',
                                                 'esp_min_deporte', 'esp_n_deporte_15min', 'esp_inters_km2']),
                    ('m_oferta_efectiva_2025', ['ofe_cupos_prim_por_nino', 'ofe_cupos_sec_por_joven',
                                                'ofe_pct_cupos_alto_desempeno', 'ofe_salud_prim_por_10mil']),
                    ('m_acceso_franjas_2025', ['acf_opp45_pico_am', 'acf_opp45_noche']),
                    ('m_acceso_mio_2025', ['acc_ai']),
                    ('m_catastro_r1_2023', ['catr_mezcla_usos'])):
        d = pd.read_sql(f'SELECT COD_DANE_A, {", ".join(cols)} FROM "{t}"', con)
        df = df.merge(d.drop_duplicates('COD_DANE_A'), on='COD_DANE_A', how='left')
    edh = pd.read_sql('SELECT COD_DANE_A, edh_anios_prom_25 FROM m_educacion_hogar WHERE vigencia = 2018', con)
    df = df.merge(edh, on='COD_DANE_A', how='left')
    df['pct_internet'] = 100 * df.TP19_INTE1 / (df.TP19_INTE1 + df.TP19_INTE2).replace(0, np.nan)
    df['pct_0_14'] = 100 * df.mc_n_e0_14 / df.mc_personas.replace(0, np.nan)
    df['pct_60'] = 100 * df.mc_n_e60 / df.mc_personas.replace(0, np.nan)

    U = df[df.esp_tipo_espacial.notna() & (df.pob18_v196 > 0)].copy().set_index('COD_DANE_A')
    w = U.pob18_v196.to_numpy(float)
    rep = {'universo': len(U), 'residentes': float(w.sum())}

    Do, o_ar, o_ge, o_acp, o_var, o_v = indice(U, OPORTUNIDADES, w, 'o')
    Da, a_ar, a_ge, a_acp, a_var, a_v = indice(U, ACTIVOS, w, 'a')
    R = pd.DataFrame(index=U.index)
    for c in Do.columns:
        R[f'o_{c}'] = Do[c].round(3)
    for c in Da.columns:
        R[f'a_{c}'] = Da[c].round(3)
    R['oportunidades'] = o_ar.round(3)
    R['oportunidades_pct'] = pct_rank_w(o_ar, w).round(1)
    R['oportunidades_geom'] = o_ge.round(1)
    R['oportunidades_acp'] = o_acp.round(3)
    R['activos'] = a_ar.round(3)
    R['activos_pct'] = pct_rank_w(a_ar, w).round(1)
    R['activos_geom'] = a_ge.round(1)

    # robustez: quintil igual en las tres agregaciones = firme; diferencia de uno = probable; más = incierto
    for nombre, (ar, ge, acp) in {'oportunidades': (o_ar, o_ge, o_acp), 'activos': (a_ar, a_ge, a_acp)}.items():
        Q = pd.concat([quintil(ar, w), quintil(ge, w), quintil(acp, w)], axis=1)
        rango = Q.max(axis=1) - Q.min(axis=1)
        R[f'{nombre}_quintil'] = Q.iloc[:, 0]
        R[f'{nombre}_robustez'] = np.select([Q.isna().any(axis=1), rango == 0, rango == 1], ['', 'firme', 'probable'],
                                            'incierto')
        rep[f'{nombre}_rho_geom'] = stats.spearmanr(ar, ge, nan_policy='omit').statistic
        rep[f'{nombre}_rho_acp'] = stats.spearmanr(ar, acp, nan_policy='omit').statistic
        rep[f'{nombre}_robustez'] = R[f'{nombre}_robustez'].value_counts(normalize=True).round(3).to_dict()
    rep['oportunidades_var_acp'] = o_var
    rep['oportunidades_cargas_acp'] = dict(zip(Do.columns, o_v.round(2)))
    rep['activos_var_acp'] = a_var
    rep['activos_cargas_acp'] = dict(zip(Da.columns, a_v.round(2)))

    # privaciones de oportunidades (Alkire-Foster): dominios en el 20 % inferior ponderado
    priv = pd.DataFrame({c: Do[c] <= wquant(Do[c].dropna().to_numpy(), w[Do[c].notna().to_numpy()], UMBRAL_PRIVACION)
                         for c in Do.columns}).where(Do.notna())
    R['privaciones_oportunidad'] = priv.sum(axis=1, min_count=len(Do.columns))
    multi = R.privaciones_oportunidad >= K_PRIVACIONES
    ok = R.privaciones_oportunidad.notna()
    H = np.average(multi[ok], weights=w[ok.to_numpy()])
    A = np.average(R.privaciones_oportunidad[ok & multi], weights=w[(ok & multi).to_numpy()]) / len(Do.columns)
    rep['privacion_multiple_H'] = H
    rep['privacion_multiple_A'] = A
    rep['privacion_multiple_M0'] = H * A

    # desajuste (Kaztman): activos frente a oportunidades, por medianas ponderadas
    mo = wquant(o_ar.dropna().to_numpy(), w[o_ar.notna().to_numpy()], .5)
    ma = wquant(a_ar.dropna().to_numpy(), w[a_ar.notna().to_numpy()], .5)
    # En estratos 4 a 6, activos altos con oportunidades públicas bajas indica autoprovisión en el mercado privado,
    # no privación: el índice mide sobre todo oferta pública y ciudad alcanzable (77 % de esos residentes caía ahí).
    alto = U.estrato_moda >= 4
    R['desajuste'] = np.select(
        [o_ar.isna() | a_ar.isna(), (a_ar >= ma) & (o_ar >= mo), (a_ar >= ma) & (o_ar < mo) & alto,
         (a_ar >= ma) & (o_ar < mo), (a_ar < ma) & (o_ar >= mo)],
        ['', 'integrada', 'autoprovision', 'activos sin oportunidades', 'oportunidades sin activos'],
        'exclusion acumulada')
    rep['desajuste'] = {k: round(float(w[(R.desajuste == k).to_numpy()].sum() / w.sum()), 3)
                        for k in R.desajuste.unique()}

    # tipologías de composición del hogar (descriptivas, sin valoración): k-medias, k = 6, manzanas con ≥ 10 hogares
    C = U[COMPOSICION].copy()
    elegibles = (U.mc_hogares >= 10) & C.notna().all(axis=1)
    X = C[elegibles]
    Xz = ((X - X.mean()) / X.std()).to_numpy()
    rng = np.random.default_rng(20261027)
    mejor = None
    for _ in range(12):
        cent = Xz[rng.choice(len(Xz), 6, replace=False)]
        for _ in range(200):
            lab = np.argmin(((Xz[:, None, :] - cent[None]) ** 2).sum(-1), axis=1)
            nuevo = np.array([Xz[lab == j].mean(0) if (lab == j).any() else cent[j] for j in range(6)])
            if np.allclose(nuevo, cent):
                break
            cent = nuevo
        inercia = ((Xz - cent[lab]) ** 2).sum()
        if mejor is None or inercia < mejor[0]:
            mejor = (inercia, lab.copy(), cent.copy())
    _, lab, cent = mejor
    Pz = pd.DataFrame(cent, columns=COMPOSICION)
    nombres = {}
    for j, f in Pz.iterrows():
        if f.eb18_hog_extra > .8:
            n = 'varios hogares por vivienda'
        elif f.mc_pct_hog_nopar > .8:
            n = 'hogares con no parientes'
        elif f.mc_pct_unip > .8:
            n = 'unipersonal envejecido'
        elif f.mc_pct_jef_fem > .8:
            n = 'familias con ninos y jefatura femenina'
        elif f.pct_0_14 > 0:
            n = 'familias con ninos'
        else:
            n = 'hogares maduros'
        nombres[j] = n if n not in nombres.values() else f'{n} ({j})'
    R['tipo_hogar'] = pd.Series([nombres[j] for j in lab], index=X.index).reindex(R.index).fillna('')
    perfiles = X.groupby(lab).mean().round(2)
    perfiles.index = [nombres[j] for j in perfiles.index]
    perfiles['pct_residentes'] = (100 * pd.Series(w[elegibles.to_numpy()]).groupby(lab).sum().values / w.sum()).round(1)
    rep['tipologias'] = perfiles
    R['estrato_moda'] = U.estrato_moda
    R = R.reset_index()
    R.drop(columns=['estrato_moda']).to_csv(salida, index=False)
    t = R.assign(w=U.pob18_v196.reindex(R.COD_DANE_A).to_numpy(),
                 grupo=np.where(R.estrato_moda <= 2, '1-2', np.where(R.estrato_moda == 3, '3', '4-6')))
    t = t[(t.desajuste != '') & R.estrato_moda.notna()]
    tab = t.groupby(['grupo', 'desajuste']).w.sum().unstack(fill_value=0)
    rep['desajuste_por_estrato'] = (100 * tab.div(tab.sum(axis=1), axis=0)).round(1)
    print('universo', rep['universo'], 'residentes', int(rep['residentes']))
    for k, v in rep.items():
        if k not in ('universo', 'residentes'):
            print(k, v if not isinstance(v, pd.DataFrame) else '\n' + v.to_string())
    perfiles.to_csv(salida.replace('.csv', '') + '.tipologias.csv')
    # comparación interna con el índice de capacidades de la fase 1 (solo una cifra agregada)
    # (el módulo cyc_ es interno: se lee solo aquí, en la base central, y no entra al CSV de salida)
    try:
        cyc = pd.read_sql('SELECT COD_DANE_A, cyc_capacidades FROM m_cargas_capacidades_2025', con)
        m = R.merge(cyc, on='COD_DANE_A')
        print('rho con cyc_capacidades (fase 1):',
              round(stats.spearmanr(m.oportunidades, m.cyc_capacidades, nan_policy='omit').statistic, 3))
    except Exception as e:  # base sin el módulo interno
        print('sin comparación con cyc_capacidades:', e)
    print('rho oportunidades-activos:', round(stats.spearmanr(R.oportunidades, R.activos, nan_policy='omit').statistic, 3))


if __name__ == '__main__':
    main(*sys.argv[1:])
