#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$TaskName = 'HLA PostgreSQL Auto Backup',
    [string]$BackupScriptPath = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_Postgres_AutoBackup.ps1',
    [string]$ScriptArguments = '',

    # Specify the account that will always run this task
    # Examples:
    #   MYDOMAIN\hla_backup
    #   PCNAME\SomeUser
    [Parameter(Mandatory = $true)]
    [string]$TaskUser
)

$ErrorActionPreference = 'Stop'

$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if ($TaskName -match '[\\/]') {
    throw "TaskName must not contain path separators. Use a plain task name, for example 'HLA PostgreSQL Auto Backup'."
}

if (-not (Test-Path -LiteralPath $BackupScriptPath)) {
    throw "Main backup script file was not found: $BackupScriptPath"
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

$extraArgs = ''

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    $extraArgs = " $ScriptArguments"
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BackupScriptPath`"$extraArgs"

# One task for the entire computer
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
Write-Host "Script: $BackupScriptPath"

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    Write-Host "Script arguments: $ScriptArguments"
}

Write-Host ''
Write-Host 'To start immediately:'
Write-Host "Start-ScheduledTask -TaskName `"$TaskName`""
