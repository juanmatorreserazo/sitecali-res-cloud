<#
Arma el paquete PÚBLICO de la base canónica de SITECALI-Res para el curso de nube.

Lee el inventario de la canónica (03_productos\base_sitecali\inventario_canonica_*.csv, el más reciente) y copia de
02_canonica solo los archivos con condición «abierta/pública», más los catálogos, manifiestos, pruebas, parámetros y
el LEEME de la canónica, y el propio inventario. Nunca copia las bóvedas reservadas, los registros por persona, las
réplicas internas, los arreglos de personas mayores ni los conteos de eventos por manzana, aunque el inventario los
marcara como públicos por error. No es el paquete portátil de armar_paquete_portatil.bat: ese lleva también archivos
internos; este lleva solo lo público.

Uso, desde PowerShell en la carpeta del repositorio (C:\SITECALI\sitecali-res-cloud):
  powershell -ExecutionPolicy Bypass -File .\herramientas\armar_paquete_curso.ps1
  powershell -ExecutionPolicy Bypass -File .\herramientas\armar_paquete_curso.ps1 -Origen "D:\otra\SITECALI-Res"

Deja datos\canonica_publica_AAAAMMDD.zip y escribe su huella SHA-256. Compatible con Windows PowerShell 5.1.
#>
param(
  [string]$Origen = "C:\Users\X1 Carbon\Documents\Doc_Lenovo_T14_01-11-2023\JuanMa\SITECALI-Res",
  [string]$Destino = (Join-Path (Split-Path -Parent $PSScriptRoot) "datos")
)
$ErrorActionPreference = "Stop"

$canonica = Join-Path $Origen "02_canonica"
if (-not (Test-Path -LiteralPath $canonica)) { throw "No existe $canonica. Revise el parámetro -Origen." }
$inv = Get-ChildItem -LiteralPath (Join-Path $Origen "03_productos\base_sitecali") -Filter "inventario_canonica_*.csv" |
       Sort-Object Name | Select-Object -Last 1
if (-not $inv) { throw "No se encontró inventario_canonica_*.csv en 03_productos\base_sitecali." }

# Rutas que nunca salen, aunque el inventario diga otra cosa (mismos patrones que config\canonica.conf).
$prohibidos = @("eventos_reservado/*", "educacion_reservado/*", "*_registros_*", "*replicas*", "*mayores_arreglos*",
                "*conteos_eventos*", "*simat_2025_*")
function Es-Prohibido([string]$ruta) {
  foreach ($p in $prohibidos) { if ($ruta.ToLower() -like $p) { return $true } }
  return $false
}

$fecha = Get-Date -Format "yyyyMMdd"
# La carpeta temporal va dentro de datos\ (sin espacios ni nombres cortos 8.3, y Git la ignora).
New-Item -ItemType Directory -Path $Destino -Force | Out-Null
$tmp = Join-Path $Destino ".tmp_canonica_publica"
if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null

# La condición se arma con el código del carácter «ú» para que la comparación no dependa de cómo Windows
# PowerShell 5.1 lea este archivo (sin marca de orden de bytes lo lee como ANSI y «ú» llegaría alterada).
$condPublica = "abierta/p" + [char]0x00FA + "blica"
$filas = Import-Csv -LiteralPath $inv.FullName -Encoding UTF8
$copiados = 0; $bytes = 0; $retenidos = @()
foreach ($f in $filas) {
  $ruta = $f.archivo
  if ($f.condicion -ne $condPublica) { continue }
  if (Es-Prohibido $ruta) { $retenidos += $ruta; continue }
  $src = Join-Path $canonica ($ruta -replace "/", "\")
  if (-not (Test-Path -LiteralPath $src)) { Write-Warning "El inventario lista $ruta pero no está en la carpeta"; continue }
  $dst = Join-Path $tmp ($ruta -replace "/", "\")
  New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
  Copy-Item -LiteralPath $src -Destination $dst
  $copiados++; $bytes += (Get-Item -LiteralPath $src).Length
}

# Documentación de la canónica que hace falta para verificar y filtrar: catálogos, manifiestos, pruebas, parámetros,
# LEEME, el manifiesto de los rásteres de 20 m y el inventario.
$doc = Get-ChildItem -LiteralPath $canonica -File | Where-Object {
  $_.Name -like "catalogo_canonica_*.csv" -or $_.Name -like "manifiesto_*.csv" -or $_.Name -like "pruebas_*.json" -or
  $_.Name -like "parametros_*.csv" -or $_.Name -eq "LEEME.md" }
foreach ($d in $doc) { Copy-Item -LiteralPath $d.FullName -Destination $tmp }
$mob = Join-Path $canonica "ciudad\ob_20m\manifiesto_ob_20m.json"
if (Test-Path -LiteralPath $mob) {
  New-Item -ItemType Directory -Path (Join-Path $tmp "ciudad\ob_20m") -Force | Out-Null
  Copy-Item -LiteralPath $mob -Destination (Join-Path $tmp "ciudad\ob_20m")
}
Copy-Item -LiteralPath $inv.FullName -Destination $tmp

$zip = Join-Path $Destino "canonica_publica_$fecha.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
# El zip se arma con .NET y nombres con «/»: Compress-Archive de Windows PowerShell 5.1 guarda las rutas con «\», y
# en Linux esos nombres no se leen como carpetas.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archivo = [System.IO.Compression.ZipFile]::Open($zip, [System.IO.Compression.ZipArchiveMode]::Create)
try {
  $base = (Get-Item -LiteralPath $tmp).FullName.TrimEnd("\") + "\"
  foreach ($a in (Get-ChildItem -LiteralPath $base -Recurse -File)) {
    $nombre = $a.FullName.Substring($base.Length) -replace "\\", "/"
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archivo, $a.FullName, $nombre,
      [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
  }
} finally { $archivo.Dispose() }
Remove-Item -LiteralPath $tmp -Recurse -Force

$h = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
Write-Host ("Inventario: {0}" -f $inv.Name)
Write-Host ("Archivos públicos copiados: {0} ({1:N1} MB) y {2} documentos de la canónica" -f $copiados, ($bytes / 1MB), ($doc.Count + 2))
if ($retenidos.Count -gt 0) { Write-Host ("Retenidos por ruta prohibida: {0}" -f ($retenidos -join ", ")) }
Write-Host ("Paquete: {0} ({1:N1} MB)" -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB))
Write-Host ("SHA256: {0}" -f $h)
