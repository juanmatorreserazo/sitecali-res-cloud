#!/usr/bin/env python3
"""Genera los datos del visor por barrio a partir del corte público de SITECALI-Res.

Corre dentro del servidor del Módulo 1 y lee PostGIS con el rol «lector_publico», que solo ve el esquema
«publico»: lo que no pasó la compuerta de divulgación no puede llegar al visor.

Uso (desde Windows, en la carpeta modulo1):
  vagrant ssh servidor -c "python3 /sitecali-repo/modulo2/visor/exportar_visor.py"
Deja dos archivos en /tmp/sitecali_visor del servidor:
  datos.js      los datos que lee sitio/index.html (solo ASCII, una línea por barrio)
  resumen.txt   qué tablas leyó, qué lentes armó y de qué corte salen

Solo usa la biblioteca estándar de Python y el cliente psql. La clave del lector se toma del archivo de
credenciales del servidor y nunca se imprime. Para pruebas se puede dar por la variable PGPASSWORD.
"""
import json
import os
import subprocess
import sys

HOST = os.environ.get("SITECALI_PGHOST", "192.168.56.10")
BD = os.environ.get("SITECALI_PGDB", "sitecali")
ROL = "lector_publico"
CLAVE = "/srv/sitecali/credenciales/lector_publico.txt"
SALIDA = os.environ.get("SITECALI_VISOR_SALIDA", "/tmp/sitecali_visor")
K = 5                 # regla de divulgación: todo conteo publicado es 0 o al menos K
TOLERANCIA_M = 5      # simplificación de los polígonos de barrio, en metros
TASA = "homicidio_tasa_barrio_2018_2024"

# Lentes opcionales: promedio de las manzanas del barrio ponderado por residentes de 2018.
# Una lente entra aquí solo después de confirmar en el catálogo qué mide y en qué sentido se lee.
# (tabla, columna, vigencia o None, identificador, título, unidad, decimales)
OPCIONALES = [
    ("marco_educacion_hogar", "edh_anios_prom_25", "2018", "educacion",
     "Años de educación", "años promedio, personas de 25 años o más, censo 2018", 1),
]


def clave():
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    r = subprocess.run(["sudo", "cat", CLAVE], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("No se pudo leer la clave del lector público en " + CLAVE)
    return r.stdout.strip()


ENTORNO = dict(os.environ, PGPASSWORD=clave(), PGCONNECT_TIMEOUT="10")


def q(sql):
    r = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", HOST, "-U", ROL, "-d", BD, "-c", sql],
                       capture_output=True, text=True, env=ENTORNO)
    if r.returncode != 0:
        sys.exit("La consulta falló:\n" + r.stderr.strip())
    return [l for l in r.stdout.split("\n") if l != ""]


def columnas(tabla):
    return set(q("SELECT column_name FROM information_schema.columns "
                 f"WHERE table_schema = 'publico' AND table_name = '{tabla}'"))


def filtro_vigencia(tabla, vigencia):
    return f" WHERE vigencia::text = '{vigencia}'" if vigencia and "vigencia" in columnas(tabla) else ""


resumen = []
c_tasa = columnas(TASA)
falta = {"unidad", "nombre", "comuna", "residentes_2018", "n", "tasa_eb", "ic_inf", "ic_sup", "estado",
         "advertencia", "geom"} - c_tasa
if falta:
    sys.exit(f"publico.{TASA} no existe o le faltan columnas: {sorted(falta)}")
resumen.append(f"publico.{TASA}: geometría de barrios, nombre, comuna y tasa suavizada")

# Regla de divulgación, comprobada otra vez antes de exportar.
malos = q(f"SELECT count(*) FROM publico.{TASA} WHERE n > 0 AND n < {K}")[0]
if malos != "0":
    sys.exit(f"{malos} barrios tienen un conteo entre 1 y {K - 1}: no se exporta nada.")

lentes = [{
    "id": "tasa", "titulo": "Homicidios", "unidad": "tasa suavizada por 100.000 años-persona, 2018–2024",
    "decimales": 1,
    "nota": "Tasa bayesiana empírica con la población de referencia corregida por omisión como denominador. "
            "Los barrios con menos de cinco víctimas en el periodo, o cuyo conteo podría deducirse por resta, "
            "aparecen como suprimidos."}]

# Población por barrio: suma de la población de referencia de las manzanas cuyo punto interior cae en el barrio.
con_pob = {"cod_dane_a", "geom"} <= columnas("manzanas_geom") and \
          {"cod_dane_a", "vigencia", "pr_total"} <= columnas("marco_poblacion_referencia")
ult = None
uniones, agregados = [], []
if con_pob:
    ult = q("SELECT max(vigencia::text::int) FROM publico.marco_poblacion_referencia")[0]
    resumen.append(f"publico.manzanas_geom y publico.marco_poblacion_referencia: población 2018 y {ult} por barrio")
    uniones.append("LEFT JOIN (SELECT cod_dane_a, pr_total AS w FROM publico.marco_poblacion_referencia "
                   "WHERE vigencia::text = '2018') p18 USING (cod_dane_a)")
    uniones.append("LEFT JOIN (SELECT cod_dane_a, pr_total AS pu FROM publico.marco_poblacion_referencia "
                   f"WHERE vigencia::text = '{ult}') pult USING (cod_dane_a)")
    agregados += ["round(sum(w)::numeric) AS pob_2018", "round(sum(pu)::numeric) AS pob_ult"]
    lentes += [
        {"id": "poblacion", "titulo": "Población", "unidad": f"residentes estimados, {ult}", "decimales": 0,
         "nota": "Población de referencia: estimación corregida por omisión censal, sumada desde las manzanas."},
        {"id": "densidad", "titulo": "Densidad", "unidad": f"residentes por hectárea, {ult}", "decimales": 0,
         "nota": "Población de referencia dividida por el área total del barrio (densidad bruta)."},
        {"id": "cambio", "titulo": "Cambio de población", "unidad": f"variación porcentual, 2018 a {ult}",
         "decimales": 1, "divergente": True,
         "nota": "Variación de la población de referencia estimada entre 2018 y el último año disponible."}]
    for tabla, col, vig, ident, titulo, unidad, dec in OPCIONALES:
        if col in columnas(tabla) and "cod_dane_a" in columnas(tabla):
            uniones.append(f"LEFT JOIN (SELECT cod_dane_a, {col} AS x_{ident} FROM publico.{tabla}"
                           f"{filtro_vigencia(tabla, vig)}) t_{ident} USING (cod_dane_a)")
            agregados.append(f"round((sum(x_{ident} * w) FILTER (WHERE x_{ident} IS NOT NULL AND w > 0) / "
                             f"NULLIF(sum(w) FILTER (WHERE x_{ident} IS NOT NULL AND w > 0), 0))::numeric, {dec}) "
                             f"AS {ident}")
            desc = q("SELECT descripcion FROM publico.diccionario "
                     f"WHERE objeto = '{tabla}' AND columna_pg = '{col}' LIMIT 1")
            lentes.append({"id": ident, "titulo": titulo, "unidad": unidad, "decimales": dec,
                           "nota": "Promedio de las manzanas del barrio ponderado por sus residentes de 2018."
                                   + (" Definición del campo: " + desc[0] if desc else "")})
            resumen.append(f"publico.{tabla}.{col}: lente «{titulo}»")
        else:
            resumen.append(f"publico.{tabla}.{col}: no está en el corte público; la lente «{titulo}» se omite")
else:
    resumen.append("Sin manzanas_geom o marco_poblacion_referencia: solo se arma la lente de la tasa")

sql = f"""
WITH b AS (
  SELECT unidad::text AS id, nombre, comuna, residentes_2018, n, tasa_eb, ic_inf, ic_sup, estado, advertencia, geom
  FROM publico.{TASA} WHERE geom IS NOT NULL
)"""
if con_pob:
    sql += f""", mz AS (
  SELECT DISTINCT ON (m.cod_dane_a) m.cod_dane_a, b.id
  FROM publico.manzanas_geom m JOIN b ON ST_Intersects(b.geom, ST_PointOnSurface(m.geom))
  ORDER BY m.cod_dane_a, b.id
), agg AS (
  SELECT mz.id, {', '.join(agregados)}
  FROM mz {' '.join(uniones)}
  GROUP BY mz.id
)"""
sql += f"""
SELECT json_build_object(
  'id', b.id, 'nombre', b.nombre, 'comuna', b.comuna,
  'area_ha', round((ST_Area(b.geom) / 10000)::numeric, 1),
  'residentes_2018', b.residentes_2018, 'n', b.n, 'tasa', b.tasa_eb, 'ic_inf', b.ic_inf, 'ic_sup', b.ic_sup,
  'estado', b.estado, 'advertencia', b.advertencia,
  {"'agg', row_to_json(agg)," if con_pob else ""}
  'g', ST_AsGeoJSON(ST_SnapToGrid(ST_SimplifyPreserveTopology(b.geom, {TOLERANCIA_M}), 1), 0)::json
)::text
FROM b {"LEFT JOIN agg USING (id)" if con_pob else ""}
ORDER BY b.id"""

barrios = []
for linea in q(sql):
    r = json.loads(linea)
    g = r.pop("g") or {}
    polis = g.get("coordinates", [])
    if g.get("type") == "Polygon":
        polis = [polis]
    r["g"] = [[[int(v) for p in anillo for v in p[:2]] for anillo in poli] for poli in polis]
    a = r.pop("agg", None) or {}
    v = {"tasa": r.pop("tasa")}
    if con_pob:
        p18, pu = a.get("pob_2018"), a.get("pob_ult")
        v["poblacion"] = pu
        v["densidad"] = round(pu / r["area_ha"]) if pu is not None and r["area_ha"] else None
        v["cambio"] = round(100 * (pu - p18) / p18, 1) if pu is not None and p18 else None
        r["pob_2018"] = p18
        for o in OPCIONALES:
            if o[3] in a:
                v[o[3]] = a[o[3]]
    r["v"] = v
    barrios.append(r)
if not barrios:
    sys.exit("La consulta no devolvió barrios.")

usados = [TASA] + (["manzanas_geom", "marco_poblacion_referencia"] if con_pob else [])
proc = q("SELECT objeto || '|' || version_base || '|' || left(sha256_archivo, 12) || '|' || "
         "to_char(cargado_en, 'YYYY-MM-DD') FROM publico.procedencia WHERE objeto IN ("
         + ", ".join(f"'{t}'" for t in usados) + ") ORDER BY objeto")
corte = [dict(zip(("objeto", "version", "huella", "cargado"), l.split("|"))) for l in proc]

cabeza = {"generado": q("SELECT to_char(now(), 'YYYY-MM-DD')")[0], "k": K, "lentes": lentes, "corte": corte,
          "srs": "EPSG:9377", "barrios": []}
texto = json.dumps(cabeza, ensure_ascii=True, separators=(",", ":"))
filas = ",\n".join(json.dumps(b, ensure_ascii=True, separators=(",", ":"), allow_nan=False) for b in barrios)
texto = "window.SITECALI_VISOR = " + texto[:-3] + "[\n" + filas + "\n]};\n"

os.makedirs(SALIDA, exist_ok=True)
with open(os.path.join(SALIDA, "datos.js"), "w", encoding="ascii", newline="\n") as f:
    f.write(texto)
sup = sum(1 for b in barrios if b["estado"] != "publicado")
resumen += [f"{len(barrios)} barrios, {sup} con la tasa suprimida por la regla de divulgación (k = {K})",
            f"{len(lentes)} lentes: " + ", ".join(l["titulo"] for l in lentes),
            "corte: " + "; ".join(f"{c['objeto']} v{c['version']} {c['huella']} ({c['cargado']})" for c in corte),
            f"datos.js: {len(texto) / 1024:.0f} KB en {SALIDA}"]
with open(os.path.join(SALIDA, "resumen.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(resumen) + "\n")
print("\n".join(resumen))
