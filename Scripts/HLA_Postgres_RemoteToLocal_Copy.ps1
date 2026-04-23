#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$RemoteDbUser = $(if ($env:HLA_REMOTE_DB_USER -ne $null) { $env:HLA_REMOTE_DB_USER } else { '' }),
    [string]$RemoteDbPassword = $(if ($env:HLA_REMOTE_DB_PASSWORD -ne $null) { $env:HLA_REMOTE_DB_PASSWORD } else { '' }),
    [string]$RemoteDbHost = $(if ($env:HLA_REMOTE_DB_HOST -ne $null) { $env:HLA_REMOTE_DB_HOST } else { '' }),
    [ValidateRange(1, 65535)]
    [int]$RemoteDbPort = $(if ($env:HLA_REMOTE_DB_PORT -ne $null) { [int]$env:HLA_REMOTE_DB_PORT } else { 5432 }),
    [string]$RemoteDbName = $(if ($env:HLA_REMOTE_DB_NAME -ne $null) { $env:HLA_REMOTE_DB_NAME } else { 'hla_db' }),

    [string]$LocalDbUser = $(if ($env:HLA_APP_DB_USER -ne $null) { $env:HLA_APP_DB_USER } else { 'postgres' }),
    [string]$LocalDbPassword = $(if ($env:HLA_APP_DB_PASSWORD -ne $null) { $env:HLA_APP_DB_PASSWORD } else { '0' }),
    [string]$LocalDbHost = $(if ($env:HLA_APP_DB_HOST -ne $null) { $env:HLA_APP_DB_HOST } else { 'localhost' }),
    [ValidateRange(1, 65535)]
    [int]$LocalDbPort = $(if ($env:HLA_APP_DB_PORT -ne $null) { [int]$env:HLA_APP_DB_PORT } else { 5432 }),
    [string]$LocalDbName = $(if ($env:HLA_APP_DB_NAME -ne $null) { $env:HLA_APP_DB_NAME } else { 'hla_db_remote' }),
    [string]$LocalMaintenanceDb = 'postgres',

    [ValidateRange(1, 10080)]
    [int]$RefreshIntervalMinutes = 60,
    [ValidateRange(1, 1440)]
    [int]$RetryMinutes = 15,

    [string]$PgBinDir,
    [string]$WorkDir = 'C:\ProgramData\HLA_PostgresRemoteCopy\Work',

    [switch]$KeepDump,
    [switch]$RunOnce,
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'

if ($ListOnly -and $RunOnce) {
    Write-Output 'Parameters -ListOnly and -RunOnce cannot be used together.'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($RemoteDbHost)) {
    Write-Output 'Parameter -RemoteDbHost is required.'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($RemoteDbUser)) {
    Write-Output 'Parameter -RemoteDbUser is required.'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($RemoteDbName)) {
    Write-Output 'Parameter -RemoteDbName must not be empty.'
    exit 1
}

if ([string]::IsNullOrWhiteSpace($LocalDbName)) {
    Write-Output 'Parameter -LocalDbName must not be empty.'
    exit 1
}

if ($LocalDbName -eq $LocalMaintenanceDb) {
    Write-Output 'Local target database and local maintenance database must be different.'
    exit 1
}

# ------------------------------------------------------------
# Script state folders
# ------------------------------------------------------------
$StateDir = 'C:\ProgramData\HLA_PostgresRemoteCopy'
$LogDir = Join-Path $StateDir 'Logs'

# ------------------------------------------------------------
# Global state
# ------------------------------------------------------------
$script:RefreshInProgress = $false
$script:PgDumpPath = $null
$script:PgRestorePath = $null
$script:PsqlPath = $null

# ------------------------------------------------------------
# Single-instance guard
# ------------------------------------------------------------
$mutexName = 'Global\HLA_Postgres_RemoteToLocal_Copy'
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)

if (-not $createdNew) {
    Write-Output 'Another instance of the PostgreSQL remote-to-local copy script is already running. Exiting.'
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
        $logFile = Join-Path $LogDir 'remote_copy.log'
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
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
}

function Test-DirectoryWritable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    New-Item -ItemType Directory -Path $Path -Force | Out-Null

    $probeFile = Join-Path $Path ([System.IO.Path]::GetRandomFileName())

    try {
        Set-Content -LiteralPath $probeFile -Value 'probe' -Encoding ASCII -NoNewline
    }
    catch {
        throw "Folder is not writable: $Path. $($_.Exception.Message)"
    }
    finally {
        Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
    }
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

    throw "Unable to locate $ExecutableName. Add PostgreSQL\bin to PATH or pass -PgBinDir."
}

function Invoke-PostgresProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [AllowNull()]
        [string]$Password
    )

    $hadPreviousPassword = Test-Path Env:PGPASSWORD
    $previousPassword = $null

    if ($hadPreviousPassword) {
        $previousPassword = $env:PGPASSWORD
    }

    if ($null -ne $Password) {
        $env:PGPASSWORD = $Password
    }
    else {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

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

function Invoke-PsqlCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,

        [ValidateRange(1, 65535)]
        [int]$Port,

        [Parameter(Mandatory = $true)]
        [string]$User,

        [AllowNull()]
        [string]$Password,

        [Parameter(Mandatory = $true)]
        [string]$Database,

        [Parameter(Mandatory = $true)]
        [string]$Sql,

        [string]$Label = 'psql'
    )

    $args = @(
        '-X'
        '-w'
        '-A'
        '-t'
        '-v'
        'ON_ERROR_STOP=1'
        '-h'
        $HostName
        '-p'
        "$Port"
        '-U'
        $User
        '-d'
        $Database
        '-c'
        $Sql
    )

    $result = Invoke-PostgresProcess -ExecutablePath $script:PsqlPath -Arguments $args -Password $Password

    if ($result.ExitCode -ne 0) {
        throw "$Label failed. Exit code: $($result.ExitCode). $($result.OutputText)"
    }

    return $result.Output
}

function Quote-SqlLiteral {
    param(
        [AllowNull()]
        [string]$Value
    )

    if ($null -eq $Value) {
        return 'NULL'
    }

    return "'" + $Value.Replace("'", "''") + "'"
}

function Quote-PostgresIdentifier {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return '"' + $Value.Replace('"', '""') + '"'
}

function Get-LastNonEmptyLine {
    param(
        [AllowNull()]
        [object[]]$Lines
    )

    $value = $Lines |
        Where-Object { $_ -and $_.ToString().Trim() -ne '' } |
        Select-Object -Last 1

    if ($null -eq $value) {
        return ''
    }

    return $value.ToString().Trim()
}

function Test-RemoteConnection {
    $query = "SELECT current_database() || '|' || current_user || '|' || current_setting('server_version');"

    $output = Invoke-PsqlCommand `
        -HostName $RemoteDbHost `
        -Port $RemoteDbPort `
        -User $RemoteDbUser `
        -Password $RemoteDbPassword `
        -Database $RemoteDbName `
        -Sql $query `
        -Label 'Remote PostgreSQL connection check'

    $summary = Get-LastNonEmptyLine -Lines $output
    Write-Log "Remote connection check succeeded: $summary"
}

function Test-LocalConnection {
    $query = "SELECT current_database() || '|' || current_user || '|' || current_setting('server_version');"

    $output = Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $query `
        -Label 'Local PostgreSQL connection check'

    $summary = Get-LastNonEmptyLine -Lines $output
    Write-Log "Local maintenance connection check succeeded: $summary"
}

function Test-LocalDatabaseExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName
    )

    $query = 'SELECT 1 FROM pg_database WHERE datname = {0};' -f (Quote-SqlLiteral -Value $DatabaseName)

    $output = Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $query `
        -Label 'Local database existence check'

    return ((Get-LastNonEmptyLine -Lines $output) -eq '1')
}

function Invoke-RemoteDump {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $dumpFile = Join-Path $WorkDir ("hla_remote_copy_{0}_{1}.dump" -f $stamp, [System.Guid]::NewGuid().ToString('N'))

    Write-Log "Starting remote pg_dump from $($RemoteDbHost):$RemoteDbPort / $RemoteDbName / $RemoteDbUser"

    $args = @(
        '--format=custom'
        '--compress=6'
        '--file'
        $dumpFile
        '--host'
        $RemoteDbHost
        '--port'
        "$RemoteDbPort"
        '--username'
        $RemoteDbUser
        '--dbname'
        $RemoteDbName
        '--no-password'
    )

    $result = Invoke-PostgresProcess -ExecutablePath $script:PgDumpPath -Arguments $args -Password $RemoteDbPassword

    if ($result.ExitCode -ne 0) {
        throw "pg_dump from remote database failed. Exit code: $($result.ExitCode). $($result.OutputText)"
    }

    if (-not (Test-Path -LiteralPath $dumpFile)) {
        throw "pg_dump finished without creating the temporary dump file: $dumpFile"
    }

    $dumpItem = Get-Item -LiteralPath $dumpFile -Force
    Write-Log ("Remote dump created: {0} (size {1:N0} bytes)." -f $dumpFile, $dumpItem.Length)

    return $dumpFile
}

function New-AuxDatabaseName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Suffix
    )

    $prefix = $LocalDbName

    if ($prefix.Length -gt 30) {
        $prefix = $prefix.Substring(0, 30)
    }

    return ('{0}_{1}_{2}' -f $prefix, $Suffix, (Get-Date -Format 'yyyyMMddHHmmss'))
}

function Invoke-CreateEmptyLocalDatabase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName
    )

    if (Test-LocalDatabaseExists -DatabaseName $DatabaseName) {
        Write-Log "Dropping stale local temporary database: $DatabaseName" 'WARN'
        Invoke-DropLocalDatabaseIfExists -DatabaseName $DatabaseName
    }

    Write-Log "Creating local temporary database: $DatabaseName"

    $sql = 'CREATE DATABASE {0};' -f (Quote-PostgresIdentifier -Value $DatabaseName)

    Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $sql `
        -Label 'Local CREATE DATABASE' | Out-Null
}

function Invoke-DropLocalDatabaseIfExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName
    )

    $sql = 'DROP DATABASE IF EXISTS {0};' -f (Quote-PostgresIdentifier -Value $DatabaseName)

    Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $sql `
        -Label 'Local DROP DATABASE' | Out-Null
}

function Invoke-TerminateLocalDatabaseConnections {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName
    )

    $sql = @'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = {0}
  AND pid <> pg_backend_pid();
'@ -f (Quote-SqlLiteral -Value $DatabaseName)

    Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $sql `
        -Label 'Local connection termination' | Out-Null
}

function Invoke-RenameLocalDatabase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FromDatabaseName,

        [Parameter(Mandatory = $true)]
        [string]$ToDatabaseName
    )

    $sql = 'ALTER DATABASE {0} RENAME TO {1};' -f `
        (Quote-PostgresIdentifier -Value $FromDatabaseName), `
        (Quote-PostgresIdentifier -Value $ToDatabaseName)

    Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $LocalMaintenanceDb `
        -Sql $sql `
        -Label 'Local ALTER DATABASE RENAME' | Out-Null
}

function Invoke-LocalRestore {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName,

        [Parameter(Mandatory = $true)]
        [string]$DumpFile
    )

    Write-Log "Starting local pg_restore into temporary database: $DatabaseName"

    $args = @(
        '--host'
        $LocalDbHost
        '--port'
        "$LocalDbPort"
        '--username'
        $LocalDbUser
        '--dbname'
        $DatabaseName
        '--no-password'
        '--no-owner'
        '--no-acl'
        '--clean'
        '--if-exists'
        '--exit-on-error'
        '--single-transaction'
        $DumpFile
    )

    $result = Invoke-PostgresProcess -ExecutablePath $script:PgRestorePath -Arguments $args -Password $LocalDbPassword

    if ($result.ExitCode -ne 0) {
        throw "pg_restore into local database failed. Exit code: $($result.ExitCode). $($result.OutputText)"
    }

    Write-Log "Local pg_restore completed successfully."
}

function Get-LocalUserTableCount {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseName
    )

    $query = @'
SELECT COUNT(*)::bigint
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema');
'@

    $output = Invoke-PsqlCommand `
        -HostName $LocalDbHost `
        -Port $LocalDbPort `
        -User $LocalDbUser `
        -Password $LocalDbPassword `
        -Database $DatabaseName `
        -Sql $query `
        -Label 'Local restored database validation'

    return (Get-LastNonEmptyLine -Lines $output)
}

function Invoke-SwapLocalDatabase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TempDbName
    )

    $oldDbName = $null
    $targetExists = Test-LocalDatabaseExists -DatabaseName $LocalDbName

    if ($targetExists) {
        $oldDbName = New-AuxDatabaseName -Suffix 'old'

        Write-Log "Moving existing local database aside: $LocalDbName -> $oldDbName"
        Invoke-TerminateLocalDatabaseConnections -DatabaseName $LocalDbName
        Invoke-RenameLocalDatabase -FromDatabaseName $LocalDbName -ToDatabaseName $oldDbName
    }

    try {
        Write-Log "Publishing refreshed database: $TempDbName -> $LocalDbName"
        Invoke-RenameLocalDatabase -FromDatabaseName $TempDbName -ToDatabaseName $LocalDbName

        if ($oldDbName) {
            Write-Log "Dropping previous local database copy: $oldDbName"
            Invoke-TerminateLocalDatabaseConnections -DatabaseName $oldDbName
            Invoke-DropLocalDatabaseIfExists -DatabaseName $oldDbName
        }
    }
    catch {
        Write-Log "Database swap failed: $($_.Exception.Message)" 'ERROR'

        if ($oldDbName) {
            try {
                $targetNowExists = Test-LocalDatabaseExists -DatabaseName $LocalDbName
                $oldNowExists = Test-LocalDatabaseExists -DatabaseName $oldDbName

                if ((-not $targetNowExists) -and $oldNowExists) {
                    Write-Log "Rolling back local database name: $oldDbName -> $LocalDbName" 'WARN'
                    Invoke-RenameLocalDatabase -FromDatabaseName $oldDbName -ToDatabaseName $LocalDbName
                }
            }
            catch {
                Write-Log "Rollback after failed database swap also failed: $($_.Exception.Message)" 'ERROR'
            }
        }

        throw
    }
}

function Invoke-PostgresRemoteCopy {
    param(
        [string]$Reason
    )

    if ($script:RefreshInProgress) {
        Write-Log "Refresh is already in progress; trigger '$Reason' skipped." 'WARN'
        return $false
    }

    $script:RefreshInProgress = $true
    $dumpFile = $null
    $tempDbName = $null
    $published = $false

    try {
        Test-DirectoryWritable -Path $WorkDir

        Write-Log "Starting remote-to-local PostgreSQL copy. Reason: $Reason"

        $dumpFile = Invoke-RemoteDump
        $tempDbName = New-AuxDatabaseName -Suffix 'tmp'

        Invoke-CreateEmptyLocalDatabase -DatabaseName $tempDbName
        Invoke-LocalRestore -DatabaseName $tempDbName -DumpFile $dumpFile

        $tempTableCount = Get-LocalUserTableCount -DatabaseName $tempDbName
        Write-Log "Temporary restored database validation succeeded. User table count: $tempTableCount"

        Invoke-SwapLocalDatabase -TempDbName $tempDbName
        $published = $true

        $finalTableCount = Get-LocalUserTableCount -DatabaseName $LocalDbName
        Write-Log "Remote-to-local PostgreSQL copy completed successfully. User table count: $finalTableCount"

        return $true
    }
    catch {
        Write-Log "Remote-to-local PostgreSQL copy error: $($_.Exception.Message)" 'ERROR'
        return $false
    }
    finally {
        if ($dumpFile -and (Test-Path -LiteralPath $dumpFile) -and (-not $KeepDump)) {
            Remove-Item -LiteralPath $dumpFile -Force -ErrorAction SilentlyContinue
        }

        if ($tempDbName -and (-not $published)) {
            try {
                if (Test-LocalDatabaseExists -DatabaseName $tempDbName) {
                    Write-Log "Dropping failed temporary local database: $tempDbName" 'WARN'
                    Invoke-TerminateLocalDatabaseConnections -DatabaseName $tempDbName
                    Invoke-DropLocalDatabaseIfExists -DatabaseName $tempDbName
                }
            }
            catch {
                Write-Log "Unable to drop failed temporary local database $($tempDbName): $($_.Exception.Message)" 'WARN'
            }
        }

        $script:RefreshInProgress = $false
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
    $script:PgRestorePath = Resolve-PostgresExecutable -ExecutableName 'pg_restore.exe'
    $script:PsqlPath = Resolve-PostgresExecutable -ExecutableName 'psql.exe'

    Test-DirectoryWritable -Path $WorkDir

    Write-Log "Using pg_dump: $script:PgDumpPath"
    Write-Log "Using pg_restore: $script:PgRestorePath"
    Write-Log "Using psql: $script:PsqlPath"
    Write-Log "Remote source: $($RemoteDbHost):$RemoteDbPort / $RemoteDbName / $RemoteDbUser"
    Write-Log "Local target: $($LocalDbHost):$LocalDbPort / $LocalDbName / $LocalDbUser"
    Write-Log "Work directory: $WorkDir"
}
catch {
    Write-Log "Fatal initialization error: $($_.Exception.Message)" 'ERROR'
    Release-Resources
    exit 1
}

if ($ListOnly) {
    try {
        Test-RemoteConnection
        Test-LocalConnection

        if (Test-LocalDatabaseExists -DatabaseName $LocalDbName) {
            Write-Log "Local target database exists: $LocalDbName"
        }
        else {
            Write-Log "Local target database does not exist yet and will be created during RunOnce: $LocalDbName" 'WARN'
        }

        Write-Log 'ListOnly completed successfully. No dump or restore was performed.'
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
    $ok = Invoke-PostgresRemoteCopy -Reason 'Manual one-time run'
    $exitCode = if ($ok) { 0 } else { 1 }
    Release-Resources
    exit $exitCode
}

Write-Log 'PostgreSQL remote-to-local copy watcher started.'
Write-Log "Refresh interval: $RefreshIntervalMinutes minutes. Retry interval after failure: $RetryMinutes minutes."

try {
    while ($true) {
        $ok = Invoke-PostgresRemoteCopy -Reason 'Scheduled refresh'
        $sleepMinutes = if ($ok) { $RefreshIntervalMinutes } else { $RetryMinutes }

        Write-Log "Next refresh attempt in $sleepMinutes minutes."
        Start-Sleep -Seconds ($sleepMinutes * 60)
    }
}
finally {
    Write-Log 'PostgreSQL remote-to-local copy watcher stopped.'
    Release-Resources
}
