# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
# Stops the dashboard, the engine and the guard of THIS folder only. It does not stop Ollama.
$here = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$n = 0
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*" -and $_.CommandLine -and $_.CommandLine.Contains($here)
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $n++
}
Write-Host "AutoPost Automation stopped ($n process(es))."
