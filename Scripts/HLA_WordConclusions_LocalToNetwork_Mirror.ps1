#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Source = '',
    [string]$Destination = '',

    [int]$DebounceSeconds = 60,
    # Scheduled full sync even when no new local file events were detected.
    [int]$FullResyncMinutes = 60,

    [int]$RoboRetryCount = 2,
    [int]$RoboWaitSeconds = 10,
    [int]$RoboThreads = 4,

    [string]$IoRate = '8M',
    [string]$Threshold = '1M',

    [switch]$UseCompression,
    [switch]$RunOnce,
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Source)) {
    Write-Output 'Parameter -Source is required. Specify the local source path.'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Destination)) {
    Write-Output 'Parameter -Destination is required. Specify the network mirror path.'
    exit 1
}

if ($ListOnly -and $RunOnce) {
    Write-Output 'Parameters -ListOnly and -RunOnce cannot be used together.'
    exit 1
}

# ------------------------------------------------------------
# Script state folders
# ------------------------------------------------------------
$StateDir = 'C:\ProgramData\HLA_WordConclusions_Mirror'
$LogDir   = Join-Path $StateDir 'Logs'

# ------------------------------------------------------------
# Exclude OS housekeeping files
# that should not be copied to the network mirror
# ------------------------------------------------------------
$ExcludedFiles = @(
    'Thumbs.db',
    'ehthumbs.db',
    'desktop.ini'
)

$ExcludedDirs = @(
    '$RECYCLE.BIN',
    'System Volume Information'
)

# Additional periodic cleanup inside destination
$CleanupExactNames = @(
    'Thumbs.db',
    'ehthumbs.db',
    'desktop.ini'
)

# How often to run targeted cleanup of excluded files in destination
$ExcludedFilesCleanupHours = 12

# ------------------------------------------------------------
# Global state
# ------------------------------------------------------------
$script:SyncInProgress   = $false
$script:LastEventTime    = $null
$script:NextFullSync     = (Get-Date).AddMinutes($FullResyncMinutes)
$script:NextExcludedFilesCleanup = Get-Date
$script:RobocopyCapabilities = $null
$script:WarnedMissingRobocopyOptions = @{}

# ------------------------------------------------------------
# Single-instance guard
# ------------------------------------------------------------
$mutexName = 'Global\HLA_WordConclusions_LocalToNetwork_Mirror'
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)

if (-not $createdNew) {
    Write-Output 'Another instance of the script is already running. Exiting.'
    exit 0
}

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR')]
        [string]$Level = 'INFO'
    )

    try {
        $line = '{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}' -f (Get-Date), $Level, $Message
        $logFile = Join-Path $LogDir 'watcher.log'
        Add-Content -Path $logFile -Value $line
        Write-Output $line
    }
    catch {
        Write-Output ('[{0}] {1}' -f $Level, $Message)
    }
}

function Ensure-StateFolders {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    New-Item -ItemType Directory -Path $LogDir   -Force | Out-Null
}

function Test-PathsReady {
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Log "Source folder is unavailable: $Source" 'ERROR'
        return $false
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        try {
            New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        }
        catch {
            Write-Log "Unable to open or create destination folder: $Destination. $($_.Exception.Message)" 'ERROR'
            return $false
        }
    }

    return $true
}

function Test-DestinationWriteAccess {
    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Log "Destination folder is unavailable for write test: $Destination" 'ERROR'
        return $false
    }

    $probeFile = Join-Path $Destination ([System.IO.Path]::GetRandomFileName() + '.tmp')

    try {
        Set-Content -LiteralPath $probeFile -Value 'probe' -Encoding ASCII -NoNewline
        Remove-Item -LiteralPath $probeFile -Force -ErrorAction Stop
        return $true
    }
    catch {
        Write-Log "Destination folder is not writable: $Destination. $($_.Exception.Message)" 'ERROR'

        if (Test-Path -LiteralPath $probeFile) {
            Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
        }

        return $false
    }
}

function Get-RobocopyCapabilities {
    if ($null -ne $script:RobocopyCapabilities) {
        return $script:RobocopyCapabilities
    }

    $helpText = ''

    try {
        $helpText = (& robocopy.exe '/?' 2>&1 | Out-String)
    }
    catch {
        $helpText = ''
    }

    $script:RobocopyCapabilities = [pscustomobject]@{
        Mir = ($helpText -match '(?im)^\s*/MIR(?:\s|:)')
        Z = ($helpText -match '(?im)^\s*/Z(?:\s|:)')
        Fft = ($helpText -match '(?im)^\s*/FFT(?:\s|:)')
        Tbd = ($helpText -match '(?im)^\s*/TBD(?:\s|:)')
        Copy = ($helpText -match '(?im)^\s*/COPY:')
        Dcopy = ($helpText -match '(?im)^\s*/DCOPY:')
        It = ($helpText -match '(?im)^\s*/IT(?:\s|:)')
        Xj = ($helpText -match '(?im)^\s*/XJ(?:\s|:)')
        Mt = ($helpText -match '(?im)^\s*/MT')
        Np = ($helpText -match '(?im)^\s*/NP(?:\s|:)')
        Ts = ($helpText -match '(?im)^\s*/TS(?:\s|:)')
        Fp = ($helpText -match '(?im)^\s*/FP(?:\s|:)')
        IoRate = ($helpText -match '(?im)^\s*/IoRate:')
        Threshold = ($helpText -match '(?im)^\s*/Threshold:')
        Compress = ($helpText -match '(?im)^\s*/COMPRESS')
    }

    return $script:RobocopyCapabilities
}

function Write-RobocopyCompatibilityWarning {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $script:WarnedMissingRobocopyOptions.ContainsKey($Key)) {
        Write-Log $Message 'WARN'
        $script:WarnedMissingRobocopyOptions[$Key] = $true
    }
}

function Get-RobocopyArguments {
    param(
        [switch]$AsListOnly,
        [ValidateSet('Standard', 'Minimal')]
        [string]$CompatibilityProfile = 'Standard'
    )

    $logSuffix = if ($AsListOnly) { 'listonly' } else { 'robocopy' }
    $roboLog = Join-Path $LogDir ("{0}_{1:yyyyMMdd}.log" -f $logSuffix, (Get-Date))
    $robocopyCapabilities = Get-RobocopyCapabilities

    $args = @(
        $Source
        $Destination

        '/MIR'
        "/R:$RoboRetryCount"
        "/W:$RoboWaitSeconds"
        "/LOG+:$roboLog"
    )

    if (-not $robocopyCapabilities.Mir) {
        throw 'Current robocopy does not support /MIR. Directory mirroring cannot continue.'
    }

    if ($CompatibilityProfile -eq 'Minimal') {
        if ($AsListOnly) {
            $args += '/L'
        }

        if ($ExcludedFiles.Count -gt 0) {
            $args += '/XF'
            $args += $ExcludedFiles
        }

        if ($ExcludedDirs.Count -gt 0) {
            $args += '/XD'
            $args += $ExcludedDirs
        }

        return ,$args
    }

    if ($robocopyCapabilities.Z) {
        $args += '/Z'
    }
    else {
        Write-RobocopyCompatibilityWarning -Key 'Z' -Message 'Current robocopy does not support /Z. Restartable mode will be skipped.'
    }

    if ($robocopyCapabilities.Fft) {
        $args += '/FFT'
    }

    if ($robocopyCapabilities.Tbd) {
        $args += '/TBD'
    }

    if ($robocopyCapabilities.Copy) {
        $args += '/COPY:DAT'
    }

    if ($robocopyCapabilities.Dcopy) {
        $args += '/DCOPY:DAT'
    }
    else {
        Write-RobocopyCompatibilityWarning -Key 'DCOPY' -Message 'Current robocopy does not support /DCOPY:DAT. Directory metadata copy will be skipped.'
    }

    if ($robocopyCapabilities.Np) {
        $args += '/NP'
    }

    if ($robocopyCapabilities.Ts) {
        $args += '/TS'
    }

    if ($robocopyCapabilities.Fp) {
        $args += '/FP'
    }

    if ($robocopyCapabilities.It) {
        $args += '/IT'
    }
    else {
        Write-RobocopyCompatibilityWarning -Key 'IT' -Message 'Current robocopy does not support /IT. Tweaked files may require a later full resync.'
    }

    if ($robocopyCapabilities.Xj) {
        $args += '/XJ'
    }
    else {
        Write-RobocopyCompatibilityWarning -Key 'XJ' -Message 'Current robocopy does not support /XJ. Junction points will not be excluded automatically.'
    }

    if ($robocopyCapabilities.Mt) {
        $args += "/MT:$RoboThreads"
    }
    else {
        Write-RobocopyCompatibilityWarning -Key 'MT' -Message 'Current robocopy does not support /MT. Single-threaded copy mode will be used.'
    }

    if (-not [string]::IsNullOrWhiteSpace($IoRate)) {
        if ($robocopyCapabilities.IoRate) {
            $args += "/IORATE:$IoRate"

            if (-not [string]::IsNullOrWhiteSpace($Threshold) -and $robocopyCapabilities.Threshold) {
                $args += "/THRESHOLD:$Threshold"
            }
            elseif (-not [string]::IsNullOrWhiteSpace($Threshold)) {
                Write-RobocopyCompatibilityWarning -Key 'THRESHOLD' -Message 'Current robocopy does not support /THRESHOLD. Copy throttling threshold will be skipped.'
            }
        }
        else {
            Write-RobocopyCompatibilityWarning -Key 'IORATE' -Message 'Current robocopy does not support /IORATE. Copy throttling options will be skipped.'
        }
    }

    if ($UseCompression) {
        if ($robocopyCapabilities.Compress) {
            $args += '/COMPRESS'
        }
        else {
            Write-RobocopyCompatibilityWarning -Key 'COMPRESS' -Message 'Current robocopy does not support /COMPRESS. SMB compression will be skipped.'
        }
    }

    if ($AsListOnly) {
        $args += '/L'
    }

    if ($ExcludedFiles.Count -gt 0) {
        $args += '/XF'
        $args += $ExcludedFiles
    }

    if ($ExcludedDirs.Count -gt 0) {
        $args += '/XD'
        $args += $ExcludedDirs
    }

    return ,$args
}

function Invoke-RobocopyMirror {
    param(
        [string]$Reason,
        [switch]$AsListOnly
    )

    if ($script:SyncInProgress) {
        Write-Log "Synchronization is already in progress; trigger '$Reason' skipped." 'WARN'
        return
    }

    if (-not (Test-PathsReady)) {
        return
    }

    if (-not (Test-DestinationWriteAccess)) {
        return
    }

    $script:SyncInProgress = $true

    try {
        $args = Get-RobocopyArguments -AsListOnly:$AsListOnly -CompatibilityProfile 'Standard'

        if ($AsListOnly) {
            Write-Log "Starting robocopy in LIST ONLY mode. Reason: $Reason"
        }
        else {
            Write-Log "Starting synchronization. Reason: $Reason"
        }

        & robocopy.exe @args | Out-Null
        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 16) {
            Write-Log 'Robocopy returned exit code 16 in standard mode. Retrying with minimal compatibility options.' 'WARN'
            $args = Get-RobocopyArguments -AsListOnly:$AsListOnly -CompatibilityProfile 'Minimal'
            & robocopy.exe @args | Out-Null
            $exitCode = $LASTEXITCODE
        }

        if ($exitCode -lt 8) {
            if ($AsListOnly) {
                Write-Log "LIST ONLY completed. Robocopy exit code: $exitCode"
            }
            else {
                Write-Log "Synchronization completed. Robocopy exit code: $exitCode"
            }
        }
        else {
            if ($AsListOnly) {
                Write-Log "LIST ONLY failed. Robocopy exit code: $exitCode" 'ERROR'
            }
            else {
                Write-Log "Robocopy failed. Exit code: $exitCode" 'ERROR'
            }
        }
    }
    catch {
        Write-Log "Exception while running robocopy: $($_.Exception.Message)" 'ERROR'
    }
    finally {
        $script:SyncInProgress = $false
    }
}

function Remove-KnownExcludedFilesInDestination {
    if ($ListOnly) {
        return
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        return
    }

    Write-Log 'Starting targeted cleanup of excluded files in destination.'

    foreach ($name in $CleanupExactNames) {
        try {
            Get-ChildItem -LiteralPath $Destination -Filter $name -Recurse -Force -File -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }
        catch {
            Write-Log "Cleanup error for '$name' in destination: $($_.Exception.Message)" 'WARN'
        }
    }

    $script:NextExcludedFilesCleanup = (Get-Date).AddHours($ExcludedFilesCleanupHours)
}

function Release-Resources {
    param(
        [System.IO.FileSystemWatcher]$Watcher,
        [string[]]$EventIds
    )

    if ($Watcher) {
        try { $Watcher.EnableRaisingEvents = $false } catch {}
    }

    if ($EventIds) {
        foreach ($id in $EventIds) {
            try { Unregister-Event -SourceIdentifier $id -ErrorAction SilentlyContinue } catch {}
            try {
                Get-Event -ErrorAction SilentlyContinue |
                    Where-Object { $_.SourceIdentifier -eq $id } |
                    Remove-Event -ErrorAction SilentlyContinue
            } catch {}
        }
    }

    if ($Watcher) {
        try { $Watcher.Dispose() } catch {}
    }

    if ($mutex) {
        try { $mutex.ReleaseMutex() | Out-Null } catch {}
        try { $mutex.Dispose() } catch {}
    }
}

# ------------------------------------------------------------
# Preparation
# ------------------------------------------------------------
try {
    Ensure-StateFolders
}
catch {
    Write-Output "Unable to create script state folders: $($_.Exception.Message)"
    try { $mutex.ReleaseMutex() | Out-Null } catch {}
    try { $mutex.Dispose() } catch {}
    exit 1
}

if (-not (Test-Path -LiteralPath $Source)) {
    Write-Log "Source folder not found: $Source" 'ERROR'
    Release-Resources -Watcher $null -EventIds @()
    exit 1
}

# ------------------------------------------------------------
# List-only mode: one validation pass and exit
# ------------------------------------------------------------
if ($ListOnly) {
    try {
        Invoke-RobocopyMirror -Reason 'Manual validation run' -AsListOnly
    }
    finally {
        Release-Resources -Watcher $null -EventIds @()
    }
    exit 0
}

# ------------------------------------------------------------
# Run-once mode: one real sync pass and exit
# ------------------------------------------------------------
if ($RunOnce) {
    try {
        Remove-KnownExcludedFilesInDestination
        Invoke-RobocopyMirror -Reason 'Manual one-time run'
    }
    finally {
        Release-Resources -Watcher $null -EventIds @()
    }
    exit 0
}

# ------------------------------------------------------------
# FileSystemWatcher setup
# ------------------------------------------------------------
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $Source
$watcher.Filter = '*'
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = [System.IO.NotifyFilters]'Attributes, FileName, DirectoryName, LastWrite, CreationTime, Size'
$watcher.InternalBufferSize = 65536

$eventIds = @(
    'HLA_WORD_CONCLUSIONS_MIRROR_Changed',
    'HLA_WORD_CONCLUSIONS_MIRROR_Created',
    'HLA_WORD_CONCLUSIONS_MIRROR_Deleted',
    'HLA_WORD_CONCLUSIONS_MIRROR_Renamed',
    'HLA_WORD_CONCLUSIONS_MIRROR_Error'
)

Register-ObjectEvent -InputObject $watcher -EventName Changed -SourceIdentifier 'HLA_WORD_CONCLUSIONS_MIRROR_Changed' | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier 'HLA_WORD_CONCLUSIONS_MIRROR_Created' | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Deleted -SourceIdentifier 'HLA_WORD_CONCLUSIONS_MIRROR_Deleted' | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier 'HLA_WORD_CONCLUSIONS_MIRROR_Renamed' | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Error   -SourceIdentifier 'HLA_WORD_CONCLUSIONS_MIRROR_Error'   | Out-Null

$watcher.EnableRaisingEvents = $true
Write-Log 'Watcher for the local source folder started.'

# Initial pass
Remove-KnownExcludedFilesInDestination
Invoke-RobocopyMirror -Reason 'Initial startup'

try {
    while ($true) {
        $evt = Wait-Event -Timeout 2

        if ($evt) {
            do {
                if ($evt.SourceIdentifier -eq 'HLA_WORD_CONCLUSIONS_MIRROR_Error') {
                    Write-Log 'FileSystemWatcher reported an error or buffer overflow. A resync will be triggered.' 'WARN'
                }

                $script:LastEventTime = Get-Date

                try {
                    Remove-Event -EventIdentifier $evt.EventIdentifier -ErrorAction SilentlyContinue
                }
                catch {}

                $evt = Get-Event -ErrorAction SilentlyContinue |
                    Where-Object { $_.SourceIdentifier -in $eventIds } |
                    Select-Object -First 1
            }
            while ($evt)
        }

        if ($script:LastEventTime) {
            $elapsed = (New-TimeSpan -Start $script:LastEventTime -End (Get-Date)).TotalSeconds

            if ($elapsed -ge $DebounceSeconds) {
                Invoke-RobocopyMirror -Reason 'Changes detected in source'
                $script:LastEventTime = $null
                $script:NextFullSync = (Get-Date).AddMinutes($FullResyncMinutes)
            }
        }

        if ((Get-Date) -ge $script:NextFullSync) {
            Invoke-RobocopyMirror -Reason 'Scheduled full resync'
            $script:NextFullSync = (Get-Date).AddMinutes($FullResyncMinutes)
        }

        if ((Get-Date) -ge $script:NextExcludedFilesCleanup) {
            Remove-KnownExcludedFilesInDestination
        }
    }
}
finally {
    Write-Log 'Watcher stopped.'
    Release-Resources -Watcher $watcher -EventIds $eventIds
}
