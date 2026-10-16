# install.ps1 - instalator paczki Unitree L2 dla Windows (uruchamiany przez INSTALUJ.bat).
#  1. Python 3.11 (jesli brak: winget albo instalator z python.org)
#  2. srodowisko venv + pakiety z requirements.txt
#  3. konfiguracja sieci (network-setup.ps1)
#  4. skroty na pulpicie
#  5. test lidaru
param([switch]$SkipNetwork)
. "$PSScriptRoot\common.ps1"
Ensure-Admin $PSCommandPath
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location $Root
try { Start-Transcript -Path (Join-Path $Root 'install.log') -Force | Out-Null } catch {}

$Host.UI.RawUI.WindowTitle = 'Instalator Unitree L2'
Write-Host ""
Write-Host "  Unitree 4D LiDAR L2 - instalacja dla Windows" -ForegroundColor White
Write-Host "  Folder: $Root"

# --- 0. sanity ---
if ($Root -match '\\AppData\\Local\\Temp\\' -or $Root -match '\.zip\\') {
    Fail "Uruchamiasz instalator z wnetrza archiwum ZIP. Najpierw rozpakuj (prawy klik na ZIP > Wyodrebnij wszystko), potem uruchom INSTALUJ.bat z rozpakowanego folderu."
}
Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# --- 1. Python ---
Step "1/5 Python 3.11"
function Find-Python {
    $cands = @()
    foreach ($v in '3.11', '3.12') {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += "$p".Trim() }
        } catch {}
    }
    $cands += @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

$py = Find-Python
if (-not $py) {
    Info "Nie znaleziono Pythona 3.11/3.12 - instaluje Python 3.11 ..."
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $ErrorActionPreference = 'Continue'
        & winget install --id Python.Python.3.11 -e --scope machine --silent --accept-package-agreements --accept-source-agreements
        $ErrorActionPreference = 'Stop'
        $installed = $true
    }
    $py = Find-Python
    if (-not $py) {
        Info "Pobieram instalator z python.org ..."
        $exe = Join-Path $env:TEMP 'python-3.11.9-amd64.exe'
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $exe -UseBasicParsing
        } catch { Fail "Nie udalo sie pobrac Pythona (brak internetu?). Zainstaluj recznie Python 3.11 z python.org i uruchom instalator ponownie." }
        $p = Start-Process -FilePath $exe -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=0', 'Include_launcher=1', 'Include_test=0' -Wait -PassThru
        if ($p.ExitCode -ne 0) { Fail "Instalator Pythona zakonczyl sie bledem $($p.ExitCode)." }
        $py = Find-Python
    }
    if (-not $py) { Fail "Python zainstalowany, ale nie moge go znalezc. Uruchom INSTALUJ.bat ponownie." }
}
Ok "Python: $py"

# --- 2. venv + pakiety ---
Step "2/5 Srodowisko Pythona i pakiety (numpy, open3d, python-osc) - kilka minut"
$venvPy = Join-Path $Root 'venv\Scripts\python.exe'
$ErrorActionPreference = 'Continue'
if (Test-Path $venvPy) {
    & $venvPy -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3,11),(3,12)) else 1)"
    if ($LASTEXITCODE -ne 0) { Info "Stary venv nie pasuje - tworze od nowa"; Remove-Item (Join-Path $Root 'venv') -Recurse -Force }
}
if (-not (Test-Path $venvPy)) {
    & $py -m venv (Join-Path $Root 'venv')
    if ($LASTEXITCODE -ne 0) { Fail "Nie udalo sie utworzyc venv." }
}
& $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail "Instalacja pakietow nie powiodla sie (internet? proxy uczelni?). Szczegoly w install.log." }
$check = & $venvPy -c "import numpy, open3d, pythonosc; print('numpy', numpy.__version__, '| open3d', open3d.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "Pakiety sie zainstalowaly, ale nie daja sie zaimportowac. Szczegoly w install.log." }
$ErrorActionPreference = 'Stop'
Ok $check

# sciezka paczki dla TouchDesignera (komponent lidar_live sam uruchamia app\l2_server.py)
$kitDir = Join-Path $env:LOCALAPPDATA 'UnitreeL2'
New-Item -ItemType Directory -Force $kitDir | Out-Null
[IO.File]::WriteAllText((Join-Path $kitDir 'kit_path.txt'), $Root)
Ok "Sciezka paczki zapisana dla TouchDesignera: $kitDir\kit_path.txt"

# --- 3. siec ---
Step "3/5 Siec"
$netOk = $false
if ($SkipNetwork) {
    Warn "Pominieto (parametr -SkipNetwork)."
} else {
    try { $netOk = (& "$PSScriptRoot\network-setup.ps1" -FromInstaller) | Select-Object -Last 1 }
    catch { Warn "Konfiguracja sieci nie powiodla sie: $($_.Exception.Message)" }
    if (-not $netOk) { Info "Mozesz to zrobic pozniej: skonfiguruj-siec.bat" }
}

# --- 4. skroty ---
Step "4/5 Skroty na pulpicie"
try {
    $desk = [Environment]::GetFolderPath('Desktop')
    $ws = New-Object -ComObject WScript.Shell
    $links = @(
        @{ Name = 'Lidar L2 - podglad 3D'; Target = 'start-viewer.bat'; Icon = 'shell32.dll,13' },
        @{ Name = 'Lidar L2 - OSC';         Target = 'start-osc.bat';    Icon = 'shell32.dll,17' },
        @{ Name = 'Lidar L2 - folder';      Target = '';                 Icon = 'shell32.dll,3' }
    )
    foreach ($l in $links) {
        $s = $ws.CreateShortcut((Join-Path $desk ($l.Name + '.lnk')))
        if ($l.Target) {
            $s.TargetPath = Join-Path $Root $l.Target
        } else {
            $s.TargetPath = $Root
        }
        $s.WorkingDirectory = $Root
        $s.IconLocation = "$env:SystemRoot\System32\$($l.Icon)"
        $s.Save()
    }
    Ok "Utworzono skroty: podglad 3D, OSC, folder"
} catch { Warn "Nie udalo sie utworzyc skrotow: $($_.Exception.Message)" }

# --- 5. test ---
Step "5/5 Test lidaru"
if ($netOk) {
    Info "Nasluchuje 5 s ..."
    $ErrorActionPreference = 'Continue'
    & $venvPy (Join-Path $Root 'app\l2_receiver.py') --seconds 5 | Select-Object -First 3 | ForEach-Object { Info $_ }
    $ErrorActionPreference = 'Stop'
    Info "Jesli 'chmura 0' - lidar moze sie jeszcze nagrzewac. Sprawdz za chwile: sprawdz-lidar.bat"
} else {
    Info "Pominieto (siec nieskonfigurowana)."
}

Write-Host ""
Write-Host "  Instalacja zakonczona." -ForegroundColor Green
Write-Host "  Uruchom 'Lidar L2 - podglad 3D' z pulpitu. Szczegoly: README.md"
try { Stop-Transcript | Out-Null } catch {}
Pause-End
