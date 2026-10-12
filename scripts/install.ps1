# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
# Checks your computer, shows what you have and what is missing, installs the missing parts, connects to
# your local text model and finishes with a readiness report. Run it by double clicking INSTALL.bat.
# Safe to run again: it only does what is missing.
param([switch]$NoPause)
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

function Say($t)  { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   OK    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   NOTE  $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "   FAIL  $t" -ForegroundColor Red }
function Row($have, $name, $why, $action) {
    if ($have) { Write-Host ("   [HAVE]     {0,-34} {1}" -f $name, $why) -ForegroundColor Green }
    else       { Write-Host ("   [MISSING]  {0,-34} {1}" -f $name, $why) -ForegroundColor Yellow
                 Write-Host ("              -> {0}" -f $action) -ForegroundColor Yellow }
}
function Pause-End { if (-not $NoPause) { Read-Host "Press Enter to close" } }
function Refresh-Path {
    $env:Path = "$([Environment]::GetEnvironmentVariable('Path','Machine'));$([Environment]::GetEnvironmentVariable('Path','User'))"
}
function Have($n) { return [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Winget-Install($id) {
    Write-Host "   installing $id with winget (this can take a few minutes)..."
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
}
function Find-Python {
    foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "python")) {
        $parts = $c.Split(" "); $exe = $parts[0]
        if (-not (Have $exe)) { continue }
        try {
            $a = @(); if ($parts.Length -gt 1) { $a += $parts[1] }
            $a += @("-c", "import sys;print(sys.executable if sys.version_info>=(3,11) else '')")
            $p = (& $exe @a 2>$null | Select-Object -First 1)
            if ($p -and (Test-Path $p)) { return $p }
        } catch {}
    }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
function Find-FFmpeg {
    $f = (Get-Command ffmpeg -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $f) { $f = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName }
    return $f
}
function Find-Ollama {
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "C:\Program Files\Ollama\ollama.exe")) { if (Test-Path $p) { return $p } }
    if (Have "ollama") { return (Get-Command ollama).Source }
    return $null
}
function Ollama-Up { try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null; return $true } catch { return $false } }

Write-Host ""
Write-Host "AutoPost Automation 3.0  |  Created by Mr Blank" -ForegroundColor White
Write-Host "Social Media Automation (Local Machine)"
Write-Host "Folder: $root"

# ------------------------------------------------------------------ before we start
Say "BEFORE WE START: what you have and what you need"
$hasWinget = Have "winget"
$py = Find-Python
$ff = Find-FFmpeg
$ollama = Find-Ollama
$vpy = Join-Path $root "venv\Scripts\python.exe"
$mem = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$disk = [math]::Round((Get-PSDrive -Name ($root.Substring(0,1))).Free / 1GB)
$gpu = $null
if (Have "nvidia-smi") { $gpu = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null | Select-Object -First 1) }
Write-Host ("   Your computer: {0} GB memory, {1} GB free disk, graphics card: {2}" -f $mem, $disk, $(if ($gpu) { $gpu } else { "none found" }))
Write-Host "   Reference computer this was built on: Windows 11, Intel Core i9-14900HX, 32 GB memory, NVIDIA RTX 4060 Laptop 8 GB."
Write-Host ""
Row $hasWinget "Windows installer (winget)" "installs the other tools" "cannot continue without it, see the note below"
Row ([bool]$py) "Python 3.11 or newer" "runs the program" "the installer will install Python 3.13"
Row ([bool]$ff) "FFmpeg" "makes audio and video files" "the installer will install it"
Row ([bool]$ollama) "Ollama" "runs the text model on your computer" "the installer will install it"
Row (Ollama-Up) "Ollama running" "the program talks to it" "the installer will start it"
Row (Test-Path $vpy) "Python environment (venv)" "keeps the program's packages apart" "the installer will create it"
Row (Test-Path (Join-Path $root "config\connections.json")) "Your accounts file" "holds your account details" "the installer will create it"
Row ($mem -ge 8) "At least 8 GB memory" "the text model needs room" "this is your computer. Use a small model or add memory"
Row ($disk -ge 15) "At least 15 GB free disk" "models and posts need space" "free up space on this drive"
Row ([bool]$gpu) "NVIDIA graphics card (optional)" "only for making video on this computer" "text and image posts still work without it"
if (-not $hasWinget) {
    Write-Host ""
    Bad "winget was not found. Open the Microsoft Store, search for 'App Installer', install or update it, then run INSTALL.bat again."
    Pause-End; exit 1
}

# ------------------------------------------------------------------ 1 python
Say "1 of 7  Python"
if (-not $py) { Winget-Install "Python.Python.3.13"; $py = Find-Python }
if ($py) { Ok "Python: $py" } else { Bad "Python could not be installed. Install Python 3.13 from python.org and run this again."; Pause-End; exit 1 }

# ------------------------------------------------------------------ 2 ffmpeg
Say "2 of 7  FFmpeg"
if (-not $ff) { Winget-Install "Gyan.FFmpeg"; $ff = Find-FFmpeg }
if ($ff) { Ok "FFmpeg: $ff" } else { Warn "FFmpeg was not found. Video and audio will not work until it is installed: winget install Gyan.FFmpeg" }

# ------------------------------------------------------------------ 3 ollama
Say "3 of 7  Ollama (local text model)"
if (-not $ollama) { Winget-Install "Ollama.Ollama"; $ollama = Find-Ollama }
if ($ollama) {
    Ok "Ollama: $ollama"
    if (-not (Ollama-Up)) {
        Write-Host "   starting Ollama..."
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 30 -and -not (Ollama-Up); $i++) { Start-Sleep -Seconds 2 }
    }
    if (Ollama-Up) { Ok "Ollama is running and answering on port 11434" } else { Warn "Ollama did not answer. Open the Ollama app from the Start menu, then run INSTALL.bat again." }
} else { Bad "Ollama could not be installed. Get it from https://ollama.com and run this again." }

# ------------------------------------------------------------------ 4 packages
Say "4 of 7  Python environment and packages"
if (-not (Test-Path $vpy)) { & $py -m venv (Join-Path $root "venv") }
if (Test-Path $vpy) {
    & $vpy -m pip install --upgrade pip --quiet --disable-pip-version-check
    Write-Host "   installing packages (a few minutes the first time)..."
    & $vpy -m pip install -r (Join-Path $root "requirements.txt") --quiet --disable-pip-version-check
    if ($LASTEXITCODE -eq 0) { Ok "all packages installed" } else { Warn "some packages failed. Run: venv\Scripts\python.exe -m pip install -r requirements.txt  and read the message." }
} else { Bad "could not create the Python environment"; Pause-End; exit 1 }

# ------------------------------------------------------------------ 5 settings
Say "5 of 7  Your settings files"
$ffbin = ""
if ($ff) { $ffbin = Split-Path $ff -Parent }
& $vpy (Join-Path $root "scripts\setup_helper.py") init-config "$ffbin"

# ------------------------------------------------------------------ 6 model
Say "6 of 7  Text model"
if (Ollama-Up) {
    Write-Host "   this computer has about $mem GB of memory"
    $have = @()
    try { $have = (Invoke-RestMethod "http://localhost:11434/api/tags").models | ForEach-Object { $_.name } } catch {}
    $pick = & $vpy (Join-Path $root "scripts\setup_helper.py") pick-model $mem ($have -join ",")
    $pick = ($pick | Select-Object -Last 1).Trim()
    if ($have -contains $pick) { Ok "using the model you already have: $pick" }
    else {
        Write-Host "   downloading the model $pick (large file, please wait)..."
        & $ollama pull $pick
    }
    & $vpy (Join-Path $root "scripts\setup_helper.py") set-model $pick
} else { Warn "skipped, Ollama is not running" }

# ------------------------------------------------------------------ 7 report
Say "7 of 7  Readiness report"
if ($gpu) { Write-Host "   Your graphics card can make local video. Run INSTALL-VIDEO.bat when you want that." }
& $vpy (Join-Path $root "scripts\doctor.py")

Write-Host ""
Write-Host "Next:"
Write-Host "  1. Connect your accounts:   CONNECT.bat"
Write-Host "  2. Start the system:        START.bat"
Write-Host "  If anything above says [MISSING], the line under it says why and how to fix it."
Write-Host "  Common problems and answers: TROUBLESHOOTING.md"
Write-Host ""
Pause-End
