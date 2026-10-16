# common.ps1 - wspolne funkcje skryptow paczki Unitree L2 (dot-source: . "$PSScriptRoot\common.ps1")

$script:Root = Split-Path -Parent $PSScriptRoot

function Step($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Info($t) { Write-Host "       $t" }

function Pause-End {
    Write-Host ""
    Read-Host "Nacisnij Enter, aby zamknac okno" | Out-Null
}

function Fail($t) {
    Write-Host ""
    Write-Host "  [BLAD] $t" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch {}
    Pause-End
    exit 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Uruchamia biezacy skrypt ponownie jako administrator (okno UAC) i konczy biezacy proces.
function Ensure-Admin([string]$ScriptPath, [string[]]$ExtraArgs = @()) {
    if (Test-Admin) { return }
    Write-Host "Potrzebne sa uprawnienia administratora - potwierdz w okienku Windows (UAC)..."
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$ScriptPath`"") + $ExtraArgs
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList | Out-Null
    } catch {
        Write-Host "Anulowano - bez uprawnien administratora nie da sie skonfigurowac sieci." -ForegroundColor Red
        Pause-End
    }
    exit
}

function Get-VenvPython {
    $p = Join-Path $script:Root 'venv\Scripts\python.exe'
    if (Test-Path $p) { return $p }
    return $null
}

function Ask-YesNo([string]$Question, [bool]$Default = $true) {
    $hint = if ($Default) { '[T/n]' } else { '[t/N]' }
    $a = Read-Host "$Question $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $Default }
    return $a.Trim().ToLower().StartsWith('t') -or $a.Trim().ToLower().StartsWith('y')
}
