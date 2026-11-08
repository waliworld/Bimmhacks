<#
 bootstrap.ps1 - started by "Install Bimmhacks Apps.cmd". Makes sure Python 3, Node.js and the
 OpenSSH client are on this PC (offers to install Python / Node.js with winget if they are
 missing), then opens the Bimmhacks installer window.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path (Split-Path -Parent $here) 'bimmhacks_installer.py'
$Host.UI.RawUI.WindowTitle = 'Bimmhacks iDrive 7 App Installer'

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u"
}

function Find-Python {
    # "python.exe" in WindowsApps is only the Microsoft Store shortcut: run each candidate to be sure
    $cands = @(
        @{ exe = 'py'; args = @('-3') },
        @{ exe = 'python'; args = @() },
        @{ exe = 'python3'; args = @() }
    )
    foreach ($c in $cands) {
        if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $v = & $c.exe @($c.args + @('-c', 'import sys, tkinter; print(sys.version_info >= (3, 6))')) 2>$null
            if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq 'True') { return $c }
        } catch { }
    }
    foreach ($p in (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue)) {
        return @{ exe = $p.FullName; args = @() }
    }
    return $null
}

function Find-Node {
    $n = Get-Command node -ErrorAction SilentlyContinue
    if ($n) { return $n.Source }
    foreach ($p in @("$env:ProgramFiles\nodejs\node.exe", "${env:ProgramFiles(x86)}\nodejs\node.exe", "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Winget-Install($id, $what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget is not available. Please install $what yourself, then start this again." -ForegroundColor Yellow
        return $false
    }
    $a = Read-Host "$what is missing. Install it now with winget? (Y/n)"
    if ($a -and $a.Trim().ToLower() -notin @('y', 'yes')) { return $false }
    winget install -e --id $id --accept-source-agreements --accept-package-agreements
    Refresh-Path
    return $true
}

Write-Host ''
Write-Host '  Bimmhacks iDrive 7 App Installer' -ForegroundColor Cyan
Write-Host '  Checking this PC...'
Write-Host ''

$py = Find-Python
if (-not $py) {
    if (Winget-Install 'Python.Python.3.12' 'Python 3') { $py = Find-Python }
}
if (-not $py) {
    Write-Host 'Python 3 (with Tk) is needed: https://www.python.org/downloads/  (tick "Add python.exe to PATH")' -ForegroundColor Red
    Read-Host 'Press Enter to close'; exit 1
}

$node = Find-Node
if (-not $node) {
    if (Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js') { $node = Find-Node }
}
if (-not $node) {
    Write-Host 'Node.js LTS is needed: https://nodejs.org/' -ForegroundColor Red
    Read-Host 'Press Enter to close'; exit 1
}
$env:BIMMHACKS_NODE = $node

if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:SystemRoot\System32\OpenSSH\ssh.exe")) {
    Write-Host 'The OpenSSH client is missing: Settings > Apps > Optional features > Add a feature > "OpenSSH Client".' -ForegroundColor Red
    Read-Host 'Press Enter to close'; exit 1
}

Write-Host '  Python, Node.js and ssh found. Opening the installer window...' -ForegroundColor Green
& $py.exe @($py.args + @($installer) + $args)
if ($LASTEXITCODE -ne 0) { Read-Host 'The installer stopped with an error. Press Enter to close' }
