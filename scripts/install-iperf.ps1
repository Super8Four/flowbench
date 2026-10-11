$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $projectRoot 'bin'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$archive = Join-Path $destination 'iperf3.zip'
# Official release of this community Windows build. Pin and verify before extraction.
$url = 'https://github.com/ar51an/iperf3-win-builds/releases/download/3.22/iperf-3.22-win64.zip'
$expected = 'c9feabb3d721039508ccb81f74c2ada3ae11b9e75d33d24510573c2aa21da420'
Invoke-WebRequest -Uri $url -OutFile $archive
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'iPerf3 checksum verification failed; nothing was extracted.' }
Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
Write-Host 'Installed iPerf3 3.22 in bin/. Restart the web app to use it.'
