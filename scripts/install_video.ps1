# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
# Optional: sets up local video (needs an NVIDIA graphics card with 8 GB or more).
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$vpy = Join-Path $root "venv\Scripts\python.exe"
Write-Host ""
Write-Host "AutoPost Automation: local video setup" -ForegroundColor Cyan
if (-not (Test-Path $vpy)) { Write-Host "Run INSTALL.bat first."; Read-Host "Press Enter to close"; exit 1 }
$gpu = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $gpu = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null | Select-Object -First 1 }
if (-not $gpu) {
    Write-Host "No NVIDIA graphics card was found. Local video needs one (8 GB or more). Text and image posts do not." -ForegroundColor Yellow
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "Graphics card: $gpu"
Write-Host ""
Write-Host "This downloads about 5 GB of video libraries and about 17 GB of model files."
$a = Read-Host "Continue? (y/n)"
if ($a -notlike "y*") { exit 0 }
Write-Host "1 of 3  PyTorch for your graphics card..."
& $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu124 --disable-pip-version-check
Write-Host "2 of 3  video libraries..."
& $vpy -m pip install -r (Join-Path $root "requirements-video.txt") huggingface_hub --disable-pip-version-check
Write-Host "3 of 3  video model (Wan2.1 1.3B)..."
$dst = Join-Path $root "models\Wan2.1-T2V-1.3B-Diffusers"
& $vpy -c "from huggingface_hub import snapshot_download; snapshot_download('Wan-AI/Wan2.1-T2V-1.3B-Diffusers', local_dir=r'$dst')"
& $vpy -c "import torch;print('torch', torch.__version__, 'cuda available:', torch.cuda.is_available())"
Write-Host ""
Write-Host "Done. Run DOCTOR.bat to check." -ForegroundColor Green
Read-Host "Press Enter to close"
