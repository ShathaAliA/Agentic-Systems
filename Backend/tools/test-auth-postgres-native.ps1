#requires -Version 7.2
param(
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin',
    [string]$Python,
    [ValidateSet('tests/test_e2e_documents.py', 'tests/test_document_pipeline_postgres.py', 'tests/test_upload_retrieval_simple.py', 'tests/test_upload_rollback.py', 'tests/test_document_repairs_postgres.py', 'tests/test_documents_postgres.py', 'tests/test_document_provisioning_postgres.py', 'tests/test_document_results_postgres.py', 'tests/test_c06_result_store_postgres.py', 'tests/test_tenant_contract_postgres.py', 'tests/test_database_isolation_postgres.py', 'tests/test_core_lifecycle_postgres.py', 'tests/test_core_access_postgres.py', 'tests/test_role_policies_postgres.py', 'tests/test_authentication_postgres.py', 'tests/test_c06_authorization_postgres.py', 'tests/test_c06_lifecycle_postgres.py', 'tests/test_c06_security_postgres.py', 'tests/test_c06_recovery_postgres.py','tests/test_c06_handoff_schema_postgres.py','tests/test_joint_ask_postgres.py','tests/test_joint_ask_pgvector_postgres.py', 'tests/test_c06_sealing_postgres.py', 'tests/test_c06_conversations_postgres.py',
                 'tests/test_users_postgres.py', 'tests/test_departments_postgres.py', 'tests/test_usage_postgres.py')]
    [string]$TestFile = 'tests/test_role_policies_postgres.py'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'test-isolation.ps1')
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$testDatabase = if ($TestFile -in @('tests/test_c06_authorization_postgres.py','tests/test_c06_lifecycle_postgres.py','tests/test_c06_security_postgres.py','tests/test_c06_recovery_postgres.py','tests/test_c06_handoff_schema_postgres.py','tests/test_joint_ask_postgres.py','tests/test_c06_result_store_postgres.py','tests/test_c06_conversations_postgres.py','tests/test_c06_sealing_postgres.py')) { 'c06_result_test' } else { 'auth_test_db' }
if (-not $Python) { $Python = Join-Path $repo '.local/rbac-venv/Scripts/python.exe' }
$Python = Resolve-TestExecutable $Python
foreach ($binary in @('initdb.exe', 'pg_ctl.exe', 'createdb.exe', 'pg_dump.exe', 'pg_dumpall.exe', 'pg_restore.exe')) {
    $null = Resolve-TestExecutable (Join-Path $PostgresBin $binary)
}
$state = Join-Path $repo ('.local/native-auth-tests/' + [Guid]::NewGuid().ToString('N'))
Assert-TestState $repo 'native-auth-tests' $state
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
$previous = Enter-IsolatedTestEnvironment
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
    Checked 'createdb.exe' ($connection + @($testDatabase))
    Checked 'pg_dump.exe' ($connection + @('-d', $testDatabase, '-Fc', '-f', (Join-Path $state 'before.dump')))
    Checked 'pg_dumpall.exe' ($connection + @('--globals-only', '--no-role-passwords', '-f', (Join-Path $state 'globals-before.sql')))
    Checked 'pg_restore.exe' @('--list', (Join-Path $state 'before.dump'))
    $urlFile = Join-Path $state 'admin.json'
    @{url="postgresql+psycopg://auth_test_owner:${password}@127.0.0.1:${port}/$testDatabase"} |
        ConvertTo-Json | Set-Content -LiteralPath $urlFile
    $env:RUN_DEVELOPMENT_PROVISIONING = $null
    $env:DEV_BACKUP_ACK = $null
    $env:RUN_AUTH_POSTGRES_TESTS = '1'
    $env:AUTH_TEST_BACKUP_ACK = '1'
    $env:AUTH_TEST_NATIVE_POSTGRES = '1'
    $env:AUTH_TEST_POSTGRES_BIN = $PostgresBin
    $env:AUTH_TEST_REPORT_DIR = $state
    $env:AUTH_TEST_ADMIN_URL_FILE = $urlFile
    & $Python -m pytest -q $TestFile --isolated-postgres --junitxml (Join-Path $state 'results.xml') --tb=short -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw 'Native PostgreSQL tests failed' }
} finally {
    $stopFailed = $false
    try {
        Assert-TestState $repo 'native-auth-tests' $state
        if ($startAttempted) {
            & (Join-Path $PostgresBin 'pg_ctl.exe') -D $data status *> $null
            $statusCode = $LASTEXITCODE
            if ($statusCode -eq 0) {
                & (Join-Path $PostgresBin 'pg_ctl.exe') -D $data -w stop
                if ($LASTEXITCODE -ne 0) { $stopFailed = $true }
                & (Join-Path $PostgresBin 'pg_ctl.exe') -D $data status *> $null
                if ($LASTEXITCODE -ne 3) { $stopFailed = $true }
            } elseif ($statusCode -ne 3) { $stopFailed = $true }
        }
    } catch { $stopFailed = $true }
    finally {
        Restore-TestEnvironment $previous
        Pop-Location
    }
    Write-Host "Retained isolated cluster and private recovery artifacts: $state"
    if ($stopFailed) { throw 'Could not confirm isolated PostgreSQL shutdown; retained recovery directory above' }
}
