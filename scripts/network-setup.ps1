# network-setup.ps1 - konfiguruje karte Ethernet pod lidar Unitree L2.
#   - statyczny adres 192.168.1.2/24 BEZ bramy (internet dalej przez Wi-Fi)
#   - profil sieci Private
#   - regula zapory "Unitree L2" (przychodzace UDP 6201)
# Poprzednie ustawienia karty sa zapisywane w network-backup.json (cofa je przywroc-siec.bat).
param(
    [switch]$FromInstaller,
    [string]$HostIp = '192.168.1.2',
    [int]$PrefixLength = 24,
    [string]$LidarIp = '192.168.1.62',
    [int]$Port = 6201
)
. "$PSScriptRoot\common.ps1"
if (-not $FromInstaller) { Ensure-Admin $PSCommandPath }

$BackupFile = Join-Path $Root 'network-backup.json'
$RuleName = 'Unitree L2'

function Invoke-NetworkSetup {
Step "Konfiguracja sieci dla lidaru"

# --- przewodowe karty sieciowe (takze adaptery USB-Ethernet) ---
$adapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object {
    ($_.NdisPhysicalMedium -eq 14 -or $_.MediaType -eq '802.3') -and
    $_.InterfaceDescription -notmatch 'Wi-?Fi|Wireless|WLAN|Bluetooth'
})
if ($adapters.Count -eq 0) {
    Warn "Nie znaleziono przewodowej karty sieciowej (Ethernet)."
    Info "Jesli uzywasz przejsciowki USB-Ethernet: podlacz ja i uruchom pozniej skonfiguruj-siec.bat."
    return $false
}

# --- wybor najbardziej prawdopodobnej karty ---
$rows = @()
foreach ($a in $adapters) {
    $ips = @(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    $gw = Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue
    $score = 0
    if ($a.Status -eq 'Up') { $score += 10 }
    if ($ips | Where-Object { $_.IPAddress -eq $HostIp }) { $score += 20 }
    if ($ips | Where-Object { $_.IPAddress -like '169.254.*' }) { $score += 5 }
    if ($ips.Count -eq 0) { $score += 5 }
    if ($ips | Where-Object { $_.IPAddress -like '192.168.1.*' }) { $score += 3 }
    if (-not $gw) { $score += 2 }
    $rows += [pscustomobject]@{
        Adapter = $a; Score = $score
        IPs = ($ips | ForEach-Object { $_.IPAddress }) -join ', '
        HasGateway = [bool]$gw
    }
}
$rows = @($rows | Sort-Object Score -Descending)

Write-Host ""
Write-Host "  Lidar podlacz kablem do karty Ethernet i wlacz zasilanie (link = status Up)."
Write-Host ""
for ($i = 0; $i -lt $rows.Count; $i++) {
    $r = $rows[$i]
    $mark = if ($i -eq 0) { '*' } else { ' ' }
    $ipText = if ($r.IPs) { $r.IPs } else { '(brak IP)' }
    $gwText = if ($r.HasGateway) { ' [ma brame - moze to byc Twoj internet po kablu!]' } else { '' }
    Write-Host ("  {0}[{1}] {2,-22} {3,-13} {4}{5}" -f $mark, ($i + 1), $r.Adapter.Name, $r.Adapter.Status, $ipText, $gwText)
    Write-Host ("        {0}" -f $r.Adapter.InterfaceDescription) -ForegroundColor DarkGray
}
Write-Host ""
$ans = Read-Host "  Numer karty lidaru (Enter = 1, S = pomin konfiguracje sieci)"
if ($ans -match '^[sS]') { Warn "Pominieto konfiguracje sieci."; return $false }
$idx = 0
if ($ans -match '^\d+$') { $idx = [int]$ans - 1 }
if ($idx -lt 0 -or $idx -ge $rows.Count) { Warn "Zly numer - pomijam."; return $false }
$sel = $rows[$idx]
$ad = $sel.Adapter
$alias = $ad.Name
Ok "Wybrano: $alias ($($ad.InterfaceDescription))"

if ($ad.Status -ne 'Up') {
    Warn "Karta nie ma polaczenia (status $($ad.Status)). Konfiguracja zadziala, gdy podlaczysz lidar."
}

# --- konflikt: 192.168.1.2 na innej karcie ---
$conflict = Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceIndex -ne $ad.ifIndex }
if ($conflict) {
    Warn "Adres $HostIp jest juz ustawiony na innej karcie: $($conflict.InterfaceAlias). Najpierw go tam usun."
    return $false
}

# --- kopia zapasowa ustawien (tylko przy pierwszej konfiguracji tej karty) ---
$ipIf = Get-NetIPInterface -InterfaceIndex $ad.ifIndex -AddressFamily IPv4
$dhcpWasOn = ($ipIf.Dhcp -eq 'Enabled')
$existing = $null
if (Test-Path $BackupFile) { try { $existing = Get-Content $BackupFile -Raw | ConvertFrom-Json } catch {} }
if (-not $existing -or $existing.InterfaceAlias -ne $alias) {
    $backup = [ordered]@{
        Date           = (Get-Date).ToString('s')
        InterfaceAlias = $alias
        InterfaceDesc  = $ad.InterfaceDescription
        Dhcp           = [bool]$dhcpWasOn
        Addresses      = @(Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                           Where-Object { $_.PrefixOrigin -eq 'Manual' } |
                           ForEach-Object { @{ IP = $_.IPAddress; Prefix = $_.PrefixLength } })
        Gateways       = @(Get-NetRoute -InterfaceIndex $ad.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                           ForEach-Object { $_.NextHop })
        Dns            = @((Get-DnsClientServerAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4).ServerAddresses)
        Profile        = "$((Get-NetConnectionProfile -InterfaceIndex $ad.ifIndex -ErrorAction SilentlyContinue).NetworkCategory)"
        AddedHostIp    = $false
        DisabledDhcp   = $false
    }
} else {
    $backup = [ordered]@{}
    $existing.PSObject.Properties | ForEach-Object { $backup[$_.Name] = $_.Value }
}

# --- adres IP ---
$hasHostIp = Get-NetIPAddress -InterfaceIndex $ad.ifIndex -IPAddress $HostIp -ErrorAction SilentlyContinue
if ($hasHostIp) {
    Ok "Karta ma juz adres $HostIp"
} else {
    if ($dhcpWasOn) {
        if ($sel.HasGateway) {
            Warn "Ta karta dostaje teraz adres z DHCP i ma brame - mozliwe, ze to Twoj internet po kablu."
        }
        Info "Wylaczam DHCP na tej karcie (Wi-Fi dziala bez zmian)."
        if (-not (Ask-YesNo "  Kontynuowac?" $true)) { Warn "Pominieto."; return $false }
        Set-NetIPInterface -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -Dhcp Disabled
        Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -ne 'Manual' } | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetRoute -InterfaceIndex $ad.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
        $backup.DisabledDhcp = $true
    }
    New-NetIPAddress -InterfaceIndex $ad.ifIndex -IPAddress $HostIp -PrefixLength $PrefixLength -ErrorAction Stop | Out-Null
    $backup.AddedHostIp = $true
    Ok "Ustawiono $HostIp/$PrefixLength bez bramy"
}
$backup | ConvertTo-Json -Depth 4 | Set-Content -Path $BackupFile -Encoding UTF8
Ok "Kopia poprzednich ustawien: network-backup.json"

# --- profil sieci ---
try {
    Set-NetConnectionProfile -InterfaceIndex $ad.ifIndex -NetworkCategory Private -ErrorAction Stop
    Ok "Profil sieci: Private"
} catch {
    Warn "Nie udalo sie ustawic profilu Private (karta bez polaczenia?). Regula zapory i tak dziala dla wszystkich profili."
}

# --- zapora ---
# Nie kasujemy istniejacej reguly: usuniecie reguly zapory odcina juz otwarte gniazda
# (dzialajacy podglad/OSC przestalby odbierac az do restartu).
$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Select-Object -First 1
if ($rule) {
    Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Direction Inbound -Action Allow -Profile Any
    $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol UDP -LocalPort $Port
    Ok "Regula zapory '$RuleName' juz byla - sprawdzona (przychodzace UDP $Port)"
} else {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol UDP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Ok "Regula zapory '$RuleName': przychodzace UDP $Port"
}

# --- test ---
Start-Sleep -Seconds 2
if (Test-Connection -ComputerName $LidarIp -Count 2 -Quiet) {
    Ok "Lidar $LidarIp odpowiada na ping"
} else {
    Warn "Lidar $LidarIp nie odpowiada na ping (wylaczony? kabel? jeszcze startuje?). Sprawdz pozniej: sprawdz-lidar.bat"
}
return $true
}

try {
    $result = Invoke-NetworkSetup
} catch {
    Warn "Blad konfiguracji sieci: $($_.Exception.Message)"
    $result = $false
}
if (-not $FromInstaller) { Pause-End }
return $result
