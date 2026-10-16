# uninstall.ps1 - cofa siec, usuwa venv i skroty. Kod i zapisane skany zostaja.
. "$PSScriptRoot\common.ps1"
Ensure-Admin $PSCommandPath

Step "Odinstalowanie Unitree L2"
if (-not (Ask-YesNo "  Przywrocic siec, usunac srodowisko Pythona (venv) i skroty?" $true)) { Pause-End; exit }

& "$PSScriptRoot\network-revert.ps1" -FromUninstaller

$desk = [Environment]::GetFolderPath('Desktop')
foreach ($n in 'Lidar L2 - podglad 3D', 'Lidar L2 - OSC', 'Lidar L2 - folder') {
    $p = Join-Path $desk "$n.lnk"
    if (Test-Path $p) { Remove-Item $p -Force }
}
Ok "Usunieto skroty z pulpitu"

$venv = Join-Path $Root 'venv'
if (Test-Path $venv) { Remove-Item $venv -Recurse -Force; Ok "Usunieto venv" }

Write-Host ""
Info "Zostaly: kod w $Root i ewentualne skany w folderze scans."
Info "Python 3.11 (jesli instalowal go instalator) usuniesz w: Ustawienia > Aplikacje."
Info "Mozesz teraz po prostu skasowac caly folder."
Pause-End
