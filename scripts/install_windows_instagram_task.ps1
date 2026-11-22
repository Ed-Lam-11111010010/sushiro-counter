# One-time, opt-in Windows Task Scheduler installer. Run only on user's computer.
[CmdletBinding()]
param(
    [string]$Time = "09:00", # WINDOWS LOCAL TIME, not necessarily Hong Kong time
    [string]$PythonExe = "",
    [string]$TaskName = "SushiroInstagramReviewCandidates"
)

$ErrorActionPreference = "Stop"
if ($Time -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
    throw "Time must be local Windows HH:mm format (e.g. 09:00)."
}
$source = Join-Path $PSScriptRoot "local_instagram_bridge.py"
if (-not (Test-Path -LiteralPath $source)) {
    throw "Missing local_instagram_bridge.py beside this installer."
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}
foreach ($program in @("git.exe", "gh.exe")) {
    if (-not (Get-Command $program -ErrorAction SilentlyContinue)) {
        throw "$program is required on PATH before installing the task."
    }
}
& $PythonExe --version
if ($LASTEXITCODE -ne 0) { throw "Python failed its version check." }
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run gh auth login, then gh auth setup-git."
}

$installDir = Join-Path $env:LOCALAPPDATA "SushiroCounterInstagram"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$installed = Join-Path $installDir "local_instagram_bridge.py"
Copy-Item -LiteralPath $source -Destination $installed -Force

$arguments = '"' + $installed + '" --submit-pr'
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, "HH:mm", $null))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
# Use only the logged-in user's gh credentials. No stored password/token.
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Public Sushiro Instagram scan to draft review PR (no menu publishing)" -Force | Out-Null

Write-Output "Created/updated Windows task: $TaskName"
Write-Output "Windows local scan time: $Time (computer must be awake and user logged in)"
Write-Output "Runner installed at: $installed"
Write-Output "To dry-run now: python scripts/local_instagram_bridge.py"
Write-Output "To check task: Get-ScheduledTask -TaskName $TaskName"
Write-Output ('To remove task: Unregister-ScheduledTask -TaskName ' + $TaskName + ' -Confirm:$false')
