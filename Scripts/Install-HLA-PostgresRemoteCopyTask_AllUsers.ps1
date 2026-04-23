#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$TaskName = 'HLA PostgreSQL Remote To Local Copy',
    [string]$CopyScriptPath = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_RemoteToLocal_Copy.ps1',

    [Parameter(Mandatory = $true)]
    [string]$RemoteDbHost,
    [ValidateRange(1, 65535)]
    [int]$RemoteDbPort = 5432,
    [string]$RemoteDbName = 'hla_db',
    [Parameter(Mandatory = $true)]
    [string]$RemoteDbUser,

    [string]$LocalDbHost = 'localhost',
    [ValidateRange(1, 65535)]
    [int]$LocalDbPort = 5432,
    [string]$LocalDbName = 'hla_db_remote',

    [ValidateRange(1, 10080)]
    [int]$RefreshIntervalMinutes = 60,

    [string]$ScriptArguments = '',

    # Specify the account that will always run this task
    # Examples:
    #   MYDOMAIN\hla_sync
    #   PCNAME\SomeUser
    [Parameter(Mandatory = $true)]
    [string]$TaskUser
)

$ErrorActionPreference = 'Stop'

$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if ($TaskName -match '[\\/]') {
    throw "TaskName must not contain path separators. Use a plain task name, for example 'HLA PostgreSQL Remote To Local Copy'."
}

if (-not (Test-Path -LiteralPath $CopyScriptPath)) {
    throw "Main remote copy script file was not found: $CopyScriptPath"
}

if ([string]::IsNullOrWhiteSpace($RemoteDbHost)) {
    throw 'RemoteDbHost must not be empty.'
}

if ([string]::IsNullOrWhiteSpace($RemoteDbName)) {
    throw 'RemoteDbName must not be empty.'
}

if ([string]::IsNullOrWhiteSpace($RemoteDbUser)) {
    throw 'RemoteDbUser must not be empty.'
}

if ([string]::IsNullOrWhiteSpace($LocalDbName)) {
    throw 'LocalDbName must not be empty.'
}

$reservedParameters = @(
    'RemoteDbHost',
    'RemoteDbPort',
    'RemoteDbName',
    'RemoteDbUser',
    'LocalDbHost',
    'LocalDbPort',
    'LocalDbName',
    'RefreshIntervalMinutes'
)

foreach ($reserved in $reservedParameters) {
    if ($ScriptArguments -match ('(?i)(^|\s)-{0}(?:\s|$)' -f [regex]::Escape($reserved))) {
        throw "Do not pass -$reserved inside -ScriptArguments. Use the installer parameter -$reserved instead."
    }
}

$SecurePassword = Read-Host "Enter password for $TaskUser" -AsSecureString
$BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)

try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($BSTR)
}
finally {
    if ($BSTR -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
    }
}

$extraArgs = " -RemoteDbHost `"$RemoteDbHost`" -RemoteDbPort $RemoteDbPort -RemoteDbName `"$RemoteDbName`" -RemoteDbUser `"$RemoteDbUser`""
$extraArgs += " -LocalDbHost `"$LocalDbHost`" -LocalDbPort $LocalDbPort -LocalDbName `"$LocalDbName`""
$extraArgs += " -RefreshIntervalMinutes $RefreshIntervalMinutes"

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    $extraArgs += " $ScriptArguments"
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$CopyScriptPath`"$extraArgs"

# One long-running task for the entire computer
$Trigger = New-ScheduledTaskTrigger -AtStartup

$Principal = New-ScheduledTaskPrincipal `
    -UserId $TaskUser `
    -LogonType Password `
    -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop

    if ($existing.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
}
catch {
    # Missing task is fine
}

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $Task `
    -User $TaskUser `
    -Password $PlainPassword `
    -Force | Out-Null

Write-Host ''
Write-Host 'Task registered successfully.'
Write-Host "Task name: $TaskName"
Write-Host "Run account: $TaskUser"
Write-Host "Script: $CopyScriptPath"
Write-Host "Remote source: $($RemoteDbHost):$RemoteDbPort / $RemoteDbName / $RemoteDbUser"
Write-Host "Local target: $($LocalDbHost):$LocalDbPort / $LocalDbName"
Write-Host "Refresh interval minutes: $RefreshIntervalMinutes"

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    Write-Host "Additional script arguments: $ScriptArguments"
}

Write-Host ''
Write-Host 'To start immediately:'
Write-Host "Start-ScheduledTask -TaskName `"$TaskName`""
