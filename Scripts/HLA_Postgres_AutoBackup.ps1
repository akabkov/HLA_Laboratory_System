#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$DbUser = $(if ($env:HLA_APP_DB_USER -ne $null) { $env:HLA_APP_DB_USER } else { 'postgres' }),
    [string]$DbPassword = $(if ($env:HLA_APP_DB_PASSWORD -ne $null) { $env:HLA_APP_DB_PASSWORD } else { '0' }),
    [string]$DbHost = $(if ($env:HLA_APP_DB_HOST -ne $null) { $env:HLA_APP_DB_HOST } else { 'localhost' }),
    [ValidateRange(1, 65535)]
    [int]$DbPort = $(if ($env:HLA_APP_DB_PORT -ne $null) { [int]$env:HLA_APP_DB_PORT } else { 5432 }),
    [string]$DbName = $(if ($env:HLA_APP_DB_NAME -ne $null) { $env:HLA_APP_DB_NAME } else { 'hla_db' }),

    [string]$BackupFile = 'D:\HLA_Laboratory_System\hla_postgres_backup.dump',

    [ValidateRange(3, 86400)]
    [int]$PollSeconds = 15,

    [ValidateRange(5, 86400)]
    [int]$DebounceSeconds = 60,

    [ValidateRange(1, 10080)]
    [int]$FullBackupMinutes = 60,

    [string]$PgBinDir,

    [switch]$RunOnce,
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'

if ($ListOnly -and $RunOnce) {
    Write-Output 'Parameters -ListOnly and -RunOnce cannot be used together.'
    exit 1
}

# ------------------------------------------------------------
# Script state folders
# ------------------------------------------------------------
$StateDir = 'C:\ProgramData\HLA_PostgresBackup'
$LogDir = Join-Path $StateDir 'Logs'

# ------------------------------------------------------------
# Global state
# ------------------------------------------------------------
$script:BackupInProgress = $false
$script:LastActivityTime = $null
$script:LastSeenSignature = $null
$script:NextFullBackup = (Get-Date).AddMinutes($FullBackupMinutes)
$script:DbProbeFailed = $false
$script:PgDumpPath = $null
$script:PsqlPath = $null
$script:HasSuccessfulBackup = Test-Path -LiteralPath $BackupFile

# ------------------------------------------------------------
# Single-instance guard
# ------------------------------------------------------------
$mutexName = 'Global\HLA_Postgres_AutoBackup'
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)

if (-not $createdNew) {
    Write-Output 'Another instance of the PostgreSQL autobackup script is already running. Exiting.'
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
        $logFile = Join-Path $LogDir 'backup_watcher.log'
        Add-Content -Path $logFile -Value $line
        Write-Output $line
    }
    catch {
        Write-Output ('[{0}] {1}' -f $Level, $Message)
    }
}

function Ensure-StateFolders {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Resolve-PostgresExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutableName
    )

    if ($PgBinDir) {
        $candidate = Join-Path $PgBinDir $ExecutableName

        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }

        throw "Unable to find $ExecutableName in PostgreSQL bin directory: $PgBinDir"
    }

    $command = Get-Command $ExecutableName -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($command) {
        return $command.Source
    }

    $candidates = @()

    foreach ($root in @('C:\Program Files\PostgreSQL', 'C:\Program Files (x86)\PostgreSQL')) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }

        foreach ($dir in Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue) {
            $binDir = Join-Path $dir.FullName 'bin'
            $candidate = Join-Path $binDir $ExecutableName

            if (-not (Test-Path -LiteralPath $candidate)) {
                continue
            }

            $version = [version]'0.0'

            try {
                $version = [version]$dir.Name
            }
            catch {
                $version = [version]'0.0'
            }

            $candidates += [pscustomobject]@{
                Path = (Resolve-Path -LiteralPath $candidate).Path
                Version = $version
            }
        }
    }

    if ($candidates.Count -gt 0) {
        return ($candidates | Sort-Object Version -Descending | Select-Object -First 1).Path
    }

    throw "Unable to locate $ExecutableName. Add PostgreSQL\\bin to PATH or pass -PgBinDir."
}

function Ensure-BackupLocationReady {
    $backupDir = Split-Path -Path $BackupFile -Parent

    if ([string]::IsNullOrWhiteSpace($backupDir)) {
        throw "Unable to determine the backup directory for file: $BackupFile"
    }

    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    if (-not (Test-Path -LiteralPath $backupDir)) {
        throw "Backup directory is unavailable: $backupDir"
    }

    $probeFile = Join-Path $backupDir ([System.IO.Path]::GetRandomFileName())

    try {
        Set-Content -LiteralPath $probeFile -Value 'probe' -Encoding ASCII -NoNewline
    }
    catch {
        throw "Backup directory is not writable: $backupDir. $($_.Exception.Message)"
    }
    finally {
        Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
    }
}

function Set-HiddenFileAttribute {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $item = Get-Item -LiteralPath $Path -Force

    if (-not ($item.Attributes -band [System.IO.FileAttributes]::Hidden)) {
        $item.Attributes = $item.Attributes -bor [System.IO.FileAttributes]::Hidden
    }
}

function Invoke-PostgresProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $hadPreviousPassword = Test-Path Env:PGPASSWORD
    $previousPassword = $null

    if ($hadPreviousPassword) {
        $previousPassword = $env:PGPASSWORD
    }

    $env:PGPASSWORD = $DbPassword

    try {
        $output = & $ExecutablePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        if ($hadPreviousPassword) {
            $env:PGPASSWORD = $previousPassword
        }
        else {
            Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
        }
    }

    $outputText = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
        OutputText = $outputText
    }
}

function Get-DatabaseWriteSignature {
    $query = @'
WITH table_stats AS (
    SELECT
        COALESCE(SUM(n_tup_ins), 0)::bigint AS ins,
        COALESCE(SUM(n_tup_upd), 0)::bigint AS upd,
        COALESCE(SUM(n_tup_del), 0)::bigint AS del
    FROM pg_stat_user_tables
),
db_stats AS (
    SELECT COALESCE(MAX(stats_reset)::text, '') AS stats_reset
    FROM pg_stat_database
    WHERE datname = current_database()
)
SELECT table_stats.ins || '|' || table_stats.upd || '|' || table_stats.del || '|' || db_stats.stats_reset
FROM table_stats
CROSS JOIN db_stats;
'@

    $args = @(
        '-X'
        '-w'
        '-A'
        '-t'
        '-v'
        'ON_ERROR_STOP=1'
        '-h'
        $DbHost
        '-p'
        "$DbPort"
        '-U'
        $DbUser
        '-d'
        $DbName
        '-c'
        $query
    )

    $result = Invoke-PostgresProcess -ExecutablePath $script:PsqlPath -Arguments $args

    if ($result.ExitCode -ne 0) {
        throw "Unable to read PostgreSQL statistics. psql exit code: $($result.ExitCode). $($result.OutputText)"
    }

    $signature = $result.Output |
        Where-Object { $_ -and $_.ToString().Trim() -ne '' } |
        Select-Object -Last 1

    if ($null -eq $signature) {
        throw 'psql did not return a write-activity signature.'
    }

    return $signature.ToString().Trim()
}

function Invoke-PostgresBackup {
    param(
        [string]$Reason
    )

    if ($script:BackupInProgress) {
        Write-Log "Backup is already in progress; trigger '$Reason' skipped." 'WARN'
        return $false
    }

    $script:BackupInProgress = $true
    $tempFile = $null

    try {
        Ensure-BackupLocationReady

        $backupDir = Split-Path -Path $BackupFile -Parent
        $tempFile = Join-Path $backupDir ([System.IO.Path]::GetRandomFileName() + '.tmp')

        Write-Log "Starting PostgreSQL backup. Reason: $Reason"

        $args = @(
            '--format=custom'
            '--compress=6'
            '--file'
            $tempFile
            '--host'
            $DbHost
            '--port'
            "$DbPort"
            '--username'
            $DbUser
            '--dbname'
            $DbName
            '--no-password'
        )

        $result = Invoke-PostgresProcess -ExecutablePath $script:PgDumpPath -Arguments $args

        if ($result.ExitCode -ne 0) {
            throw "pg_dump failed. Exit code: $($result.ExitCode). $($result.OutputText)"
        }

        if (-not (Test-Path -LiteralPath $tempFile)) {
            throw "pg_dump finished without creating the temporary file: $tempFile"
        }

        Move-Item -LiteralPath $tempFile -Destination $BackupFile -Force
        Set-HiddenFileAttribute -Path $BackupFile

        $backupItem = Get-Item -LiteralPath $BackupFile -Force
        $script:HasSuccessfulBackup = $true

        Write-Log ("Backup file updated: {0} (size {1:N0} bytes)." -f $BackupFile, $backupItem.Length)
        return $true
    }
    catch {
        Write-Log "PostgreSQL backup error: $($_.Exception.Message)" 'ERROR'
        return $false
    }
    finally {
        if ($tempFile -and (Test-Path -LiteralPath $tempFile)) {
            Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
        }

        $script:BackupInProgress = $false
    }
}

function Release-Resources {
    if ($mutex) {
        try { $mutex.ReleaseMutex() | Out-Null } catch {}
        try { $mutex.Dispose() } catch {}
    }
}

try {
    Ensure-StateFolders
}
catch {
    Write-Output "Unable to create script state folders: $($_.Exception.Message)"
    Release-Resources
    exit 1
}

try {
    $script:PgDumpPath = Resolve-PostgresExecutable -ExecutableName 'pg_dump.exe'
    $script:PsqlPath = Resolve-PostgresExecutable -ExecutableName 'psql.exe'

    Ensure-BackupLocationReady

    if (Test-Path -LiteralPath $BackupFile) {
        Set-HiddenFileAttribute -Path $BackupFile
    }

    Write-Log "Using pg_dump: $script:PgDumpPath"
    Write-Log "Using psql: $script:PsqlPath"
    Write-Log "Backup file: $BackupFile"
    Write-Log "Connection settings: $($DbHost):$DbPort / $DbName / $DbUser"
}
catch {
    Write-Log "Fatal initialization error: $($_.Exception.Message)" 'ERROR'
    Release-Resources
    exit 1
}

if ($ListOnly) {
    try {
        $signature = Get-DatabaseWriteSignature
        Write-Log "Connection check succeeded. Current write signature: $signature"
        Write-Log 'ListOnly completed successfully. No real backup was created.'
        Release-Resources
        exit 0
    }
    catch {
        Write-Log "ListOnly failed: $($_.Exception.Message)" 'ERROR'
        Release-Resources
        exit 1
    }
}

if ($RunOnce) {
    try {
        $null = Get-DatabaseWriteSignature
        $ok = Invoke-PostgresBackup -Reason 'Manual one-time run'
        $exitCode = if ($ok) { 0 } else { 1 }
        Release-Resources
        exit $exitCode
    }
    catch {
        Write-Log "RunOnce failed: $($_.Exception.Message)" 'ERROR'
        Release-Resources
        exit 1
    }
}

Write-Log 'PostgreSQL activity watcher started.'

try {
    try {
        $script:LastSeenSignature = Get-DatabaseWriteSignature
        $script:DbProbeFailed = $false
        $null = Invoke-PostgresBackup -Reason 'Initial startup'

        try {
            $script:LastSeenSignature = Get-DatabaseWriteSignature
        }
        catch {}
    }
    catch {
        $script:DbProbeFailed = $true
        Write-Log "PostgreSQL is not available yet at startup. The watcher will keep waiting: $($_.Exception.Message)" 'WARN'
    }

    while ($true) {
        Start-Sleep -Seconds $PollSeconds

        $probeSucceeded = $false

        try {
            $currentSignature = Get-DatabaseWriteSignature
            $probeSucceeded = $true

            if ($script:DbProbeFailed) {
                Write-Log 'Connection to PostgreSQL has been restored.'
                $script:DbProbeFailed = $false
            }

            if (-not $script:HasSuccessfulBackup) {
                $backupOk = Invoke-PostgresBackup -Reason 'First available connection to PostgreSQL'

                if ($backupOk) {
                    $script:NextFullBackup = (Get-Date).AddMinutes($FullBackupMinutes)

                    try {
                        $currentSignature = Get-DatabaseWriteSignature
                    }
                    catch {}
                }
            }

            if ($null -eq $script:LastSeenSignature) {
                $script:LastSeenSignature = $currentSignature
            }
            elseif ($currentSignature -ne $script:LastSeenSignature) {
                if ($null -eq $script:LastActivityTime) {
                    Write-Log 'Write activity detected in PostgreSQL. Waiting for the quiet period before backup.'
                }

                $script:LastSeenSignature = $currentSignature
                $script:LastActivityTime = Get-Date
            }
        }
        catch {
            if (-not $script:DbProbeFailed) {
                Write-Log "Unable to poll PostgreSQL: $($_.Exception.Message)" 'WARN'
                $script:DbProbeFailed = $true
            }
        }

        if ($script:LastActivityTime) {
            $elapsed = (New-TimeSpan -Start $script:LastActivityTime -End (Get-Date)).TotalSeconds

            if ($elapsed -ge $DebounceSeconds) {
                $null = Invoke-PostgresBackup -Reason 'Changes detected in PostgreSQL'
                $script:LastActivityTime = $null
                $script:NextFullBackup = (Get-Date).AddMinutes($FullBackupMinutes)

                if ($probeSucceeded) {
                    try {
                        $script:LastSeenSignature = Get-DatabaseWriteSignature
                    }
                    catch {}
                }
            }
        }

        if ((Get-Date) -ge $script:NextFullBackup) {
            $null = Invoke-PostgresBackup -Reason 'Scheduled full backup'
            $script:NextFullBackup = (Get-Date).AddMinutes($FullBackupMinutes)

            if ($probeSucceeded) {
                try {
                    $script:LastSeenSignature = Get-DatabaseWriteSignature
                }
                catch {}
            }
        }
    }
}
finally {
    Write-Log 'PostgreSQL autobackup watcher stopped.'
    Release-Resources
}
