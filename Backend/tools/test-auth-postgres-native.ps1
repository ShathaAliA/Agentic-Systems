#requires -Version 7.2
param(
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin',
    [string]$Python,
    [ValidateSet('tests/test_core_lifecycle_postgres.py', 'tests/test_core_access_postgres.py', 'tests/test_role_policies_postgres.py', 'tests/test_authentication_postgres.py',
                 'tests/test_users_postgres.py', 'tests/test_departments_postgres.py', 'tests/test_usage_postgres.py')]
    [string]$TestFile = 'tests/test_role_policies_postgres.py'
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (-not $Python) { $Python = Join-Path $repo '.local/rbac-venv/Scripts/python.exe' }
$state = Join-Path $repo ('.local/native-auth-tests/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $state | Out-Null
$acl = Get-Acl -LiteralPath $state
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,
                  [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $state -AclObject $acl
$password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$passwordFile = Join-Path $state 'password'
[IO.File]::WriteAllText($passwordFile, $password)
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
if ($port -eq 5435) { throw 'Refusing original development port' }
$data = Join-Path $state 'data'
function Checked([string]$Name, [string[]]$Arguments) {
    & (Join-Path $PostgresBin $Name) @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Native test command failed: $Name" }
}
$keys = @('PGPASSWORD', 'RUN_AUTH_POSTGRES_TESTS', 'AUTH_TEST_BACKUP_ACK',
    'RUN_DEVELOPMENT_PROVISIONING', 'DEV_BACKUP_ACK', 'AUTH_TEST_ADMIN_URL_FILE', 'AUTH_TEST_NATIVE_POSTGRES', 'AUTH_TEST_REPORT_DIR', 'CENTRAL_MIGRATION_DATABASE_URL_FILE')
$previous = @{}
foreach ($key in $keys) { $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
$startAttempted = $false
Push-Location (Join-Path $repo 'Backend')
try {
    Checked 'initdb.exe' @('-D', $data, '-U', 'auth_test_owner', '--pwfile', $passwordFile,
        '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--encoding=UTF8')
    $startAttempted = $true
    Checked 'pg_ctl.exe' @('-D', $data, '-l', (Join-Path $state 'postgres.log'),
        '-o', "-h 127.0.0.1 -p $port", '-w', 'start')
    $env:PGPASSWORD = $password
    $connection = @('-h', '127.0.0.1', '-p', "$port", '-U', 'auth_test_owner')
    Checked 'createdb.exe' ($connection + @('auth_test_db'))
    Checked 'pg_dump.exe' ($connection + @('-d', 'auth_test_db', '-Fc', '-f', (Join-Path $state 'before.dump')))
    Checked 'pg_dumpall.exe' ($connection + @('--globals-only', '--no-role-passwords', '-f', (Join-Path $state 'globals-before.sql')))
    Checked 'pg_restore.exe' @('--list', (Join-Path $state 'before.dump'))
    $urlFile = Join-Path $state 'admin.json'
    @{url="postgresql+psycopg://auth_test_owner:${password}@127.0.0.1:${port}/auth_test_db"} |
        ConvertTo-Json | Set-Content -LiteralPath $urlFile
    $env:RUN_DEVELOPMENT_PROVISIONING = $null
    $env:DEV_BACKUP_ACK = $null
    $env:RUN_AUTH_POSTGRES_TESTS = '1'
    $env:AUTH_TEST_BACKUP_ACK = '1'
    $env:AUTH_TEST_NATIVE_POSTGRES = '1'
    $env:AUTH_TEST_REPORT_DIR = $state
    $env:AUTH_TEST_ADMIN_URL_FILE = $urlFile
    & $Python -m pytest -q $TestFile --isolated-postgres --junitxml (Join-Path $state 'results.xml') --tb=short -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw 'Native PostgreSQL tests failed' }
} finally {
    $stopFailed = $false
    if ($startAttempted) {
        & (Join-Path $PostgresBin 'pg_ctl.exe') -D $data -w stop
        $stopFailed = $LASTEXITCODE -ne 0
    }
    foreach ($key in $keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
    Pop-Location
    Write-Host "Retained isolated cluster and private recovery artifacts: $state"
    if ($stopFailed) { throw 'Could not confirm isolated PostgreSQL shutdown; retained recovery directory above' }
}
