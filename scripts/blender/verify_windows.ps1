param([Parameter(ValueFromRemainingArguments=$true)][string[]]$LauncherArgs)
$ErrorActionPreference = "Stop"
$Python = if ($env:WFRL_LAUNCHER_PYTHON) { $env:WFRL_LAUNCHER_PYTHON } else { "python" }
$Launcher = Join-Path $PSScriptRoot "wfrl_launcher.py"
& $Python $Launcher verify @LauncherArgs
exit $LASTEXITCODE
