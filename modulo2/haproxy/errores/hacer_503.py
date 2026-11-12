import re, sys
# Arma la página de disculpa de HAProxy (error 503) con la marca 1.0 de SITECALI-Res.
# Uso: python -I hacer_503.py <sitecali-res_logo_color.svg del paquete de marca> 503.html 503.http
# El logo se usa tal cual viene en el paquete: solo se quitan sus metadatos y la tinta pasa a currentColor
# para que la misma pieza sirva en color y en negativo (modo oscuro). La manzana conserva el encendido.
svg_path, out_html, out_http = sys.argv[1:4]
s = open(svg_path, encoding='utf8').read()
s = re.sub(r'<metadata>.*?</metadata>', '', s, flags=re.S)
s = re.sub(r'\s*xmlns:c2pa="[^"]*"', '', s)
s = re.sub(r'\s*width="\d+"\s*height="\d+"', '', s, count=1)
# Tinta -> currentColor: la misma pieza sirve en color (tinta sobre cal) y en negativo (cal sobre tinta).
s = s.replace('"#15161a"', '"currentColor"')
s = s.replace('<svg ', '<svg class="logo" role="img" aria-label="SITECALI-Res" ', 1)
html = '''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SITECALI-Res · volvemos en un momento</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono&family=Instrument+Sans:wght@400;600&family=Instrument+Serif:ital@1&display=swap" rel="stylesheet">
<style>
:root{--cal:#F4F1EA;--tinta:#15161A;--sec:#4A4C52;--filete:#D8D2C5}
@media (prefers-color-scheme:dark){:root{--cal:#15161A;--tinta:#F4F1EA;--sec:#B9B5AC;--filete:#3A3B40}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;background:var(--cal);color:var(--tinta);font:17px/1.6 'Instrument Sans',Arial,sans-serif;padding:32px 16px}
main{max-width:600px;width:100%}
.logo{display:block;width:360px;max-width:100%;height:auto;color:var(--tinta);margin:0 0 36px -18px}
.rotulo{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--sec);margin:0 0 12px}
h1{font-weight:600;font-size:34px;line-height:1.2;margin:0 0 20px}
h1 em{font-family:'Instrument Serif',Georgia,serif;font-weight:400}
p{margin:0 0 16px}
footer{max-width:600px;width:100%;margin-top:40px;padding-top:16px;border-top:1px solid var(--filete);font-size:13px;color:var(--sec)}
footer em{font-family:'Instrument Serif',Georgia,serif;font-size:15px}
</style>
</head>
<body>
<main>
LOGO
<p class="rotulo">503 · Servicio no disponible</p>
<h1>Volvemos en <em>un momento</em></h1>
<p>Te pedimos disculpas. En este instante ninguno de los servidores que muestran el visor está respondiendo, ni los de producción ni los de respaldo. No es un problema de tu conexión.</p>
<p>El balanceador revisa cada pocos segundos si alguno vuelve. Cuando uno responda, basta con recargar la página para seguir mirando la ciudad.</p>
</main>
<footer>Juan Manuel Torres Erazo · SITECALI-Res, centro de pensamiento · <em>Cali desde todos los lados</em></footer>
</body>
</html>
'''.replace('LOGO', s)
open(out_html, 'w', encoding='utf8', newline='\n').write(html)
head = 'HTTP/1.1 503 Service Unavailable\r\nCache-Control: no-cache\r\nConnection: close\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'
with open(out_http, 'wb') as f:
    f.write(head.encode('ascii') + html.encode('utf8'))
print(len(html.encode()), 'bytes html;', len(head)+len(html.encode()), 'bytes errorfile')
