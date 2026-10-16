# network-revert.ps1 - cofa zmiany zrobione przez network-setup.ps1 (na podstawie network-backup.json).
param([switch]$FromUninstaller)
. "$PSScriptRoot\common.ps1"
if (-not $FromUninstaller) { Ensure-Admin $PSCommandPath }

$BackupFile = Join-Path $Root 'network-backup.json'
$RuleName = 'Unitree L2'
$HostIp = '192.168.1.2'

Step "Przywracanie ustawien sieci"

if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $RuleName
    Ok "Usunieto regule zapory '$RuleName'"
}

if (-not (Test-Path $BackupFile)) {
    Warn "Brak network-backup.json - nie wiem, ktora karte przywrocic."
    Info "Recznie: Ustawienia > Siec > Ethernet > Edytuj przypisanie IP > Automatycznie (DHCP)."
    if (-not $FromUninstaller) { Pause-End }
    return
}

$b = Get-Content $BackupFile -Raw | ConvertFrom-Json
$alias = $b.InterfaceAlias
if (-not (Get-NetAdapter -Name $alias -ErrorAction SilentlyContinue)) {
    Warn "Karta '$alias' nie istnieje (odlaczony adapter USB?). Podlacz ja i uruchom ponownie."
    if (-not $FromUninstaller) { Pause-End }
    return
}

if ($b.AddedHostIp) {
    Remove-NetIPAddress -InterfaceAlias $alias -IPAddress $HostIp -Confirm:$false -ErrorAction SilentlyContinue
    Ok "Usunieto $HostIp z karty $alias"
}

if ($b.DisabledDhcp -or $b.Dhcp) {
    Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.PrefixOrigin -eq 'Manual' } | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -Dhcp Enabled
    Set-DnsClientServerAddress -InterfaceAlias $alias -ResetServerAddresses
    Ok "Wlaczono z powrotem DHCP na karcie $alias"
} else {
    foreach ($a in @($b.Addresses)) {
        if ($a -and -not (Get-NetIPAddress -InterfaceAlias $alias -IPAddress $a.IP -ErrorAction SilentlyContinue)) {
            New-NetIPAddress -InterfaceAlias $alias -IPAddress $a.IP -PrefixLength $a.Prefix -ErrorAction SilentlyContinue | Out-Null
        }
    }
    Ok "Karta $alias ma z powrotem poprzednie adresy statyczne"
}

if ($b.Profile -eq 'Public') {
    try { Set-NetConnectionProfile -InterfaceAlias $alias -NetworkCategory Public -ErrorAction Stop } catch {}
}

Remove-Item $BackupFile -Force
Ok "Gotowe."
Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Format-Table IPAddress, PrefixLength, PrefixOrigin -AutoSize | Out-String | Write-Host
if (-not $FromUninstaller) { Pause-End }
