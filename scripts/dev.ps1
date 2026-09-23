$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
$env:DJANGO_DEBUG = 'true'
$env:CACACA_LOCAL_ENV = 'true'
& "$projectDirectory/.venv/Scripts/python.exe" manage.py migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$projectDirectory/.venv/Scripts/python.exe" manage.py runserver 127.0.0.1:8000
