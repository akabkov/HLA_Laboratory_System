#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$TaskName = 'HLA Local To Network Mirror',
    [string]$SyncScriptPath = 'C:\Program Files\HLA_Laboratory_System\_internal\Scripts\HLA_LocalToNetwork_Mirror.ps1',
    [string]$Source = 'D:\HLA_Laboratory_System',
    [Parameter(Mandatory = $true)]
    [string]$Destination,
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
    throw "TaskName must not contain path separators. Use a plain task name, for example 'HLA Local To Network Mirror'."
}

if (-not (Test-Path -LiteralPath $SyncScriptPath)) {
    throw "Main script file was not found: $SyncScriptPath"
}

if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = 'D:\HLA_Laboratory_System'
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    throw "Destination must not be empty. Specify the network mirror path via -Destination."
}

if ($ScriptArguments -match '(?i)(^|\s)-Source(?:\s|$)') {
    throw "Do not pass -Source inside -ScriptArguments. Use the installer parameter -Source instead."
}

if ($ScriptArguments -match '(?i)(^|\s)-Destination(?:\s|$)') {
    throw "Do not pass -Destination inside -ScriptArguments. Use the installer parameter -Destination instead."
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

$extraArgs = " -Source `"$Source`" -Destination `"$Destination`""

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    $extraArgs += " $ScriptArguments"
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SyncScriptPath`"$extraArgs"

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

Write-Host ""
Write-Host "Task registered successfully."
Write-Host "Task name: $TaskName"
Write-Host "Run account: $TaskUser"
Write-Host "Script: $SyncScriptPath"
Write-Host "Source: $Source"
Write-Host "Destination: $Destination"

if (-not [string]::IsNullOrWhiteSpace($ScriptArguments)) {
    Write-Host "Additional script arguments: $ScriptArguments"
}

Write-Host ""
Write-Host "To start immediately:"
Write-Host "Start-ScheduledTask -TaskName `"$TaskName`""
