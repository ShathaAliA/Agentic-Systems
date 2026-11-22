#requires -Version 7.2
param(
    [string]$Docker = "docker",
    [string]$Image = "ai-platform-core:test",
    [ValidateSet("postgres:17-alpine", "pgvector/pgvector:pg17")]
    [string]$PostgresImage = "postgres:17-alpine",
    [ValidateSet("tests/test_e2e_documents.py", "tests/test_document_pipeline_postgres.py", "tests/test_upload_retrieval_simple.py", "tests/test_upload_rollback.py", "tests/test_document_repairs_postgres.py", "tests/test_documents_postgres.py", "tests/test_document_provisioning_postgres.py", "tests/test_document_results_postgres.py", "tests/test_c06_result_store_postgres.py", "tests/test_tenant_contract_postgres.py", "tests/test_database_isolation_postgres.py", "tests/test_core_lifecycle_postgres.py", "tests/test_authentication_postgres.py", "tests/test_departments_postgres.py", "tests/test_users_postgres.py", "tests/test_usage_postgres.py", "tests/test_role_policies_postgres.py", "tests/test_core_access_postgres.py", "tests/test_authentication_postgres.py", "tests/test_c06_authorization_postgres.py", "tests/test_c06_lifecycle_postgres.py", "tests/test_c06_security_postgres.py", "tests/test_c06_recovery_postgres.py", "tests/test_c06_handoff_schema_postgres.py", "tests/test_c06_handoff_service_postgres.py", "tests/test_joint_ask_postgres.py", "tests/test_joint_ask_pgvector_postgres.py", "tests/test_c06_sealing_postgres.py", "tests/test_c06_conversations_postgres.py")]
    [string]$TestFile = "tests/test_authentication_postgres.py",
    # C06 only: dump the finished test database, restore it into a NEW database in the same
    # throwaway cluster and verify the restored copy. No other database is read or changed.
    [switch]$RestoreDrill
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'test-isolation.ps1')
$Docker = Resolve-TestExecutable $Docker
Assert-LocalTestDocker $Docker
$testUser = Get-TestClientUser
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$backendRoot = Join-Path $repoRoot "Backend"
$testDatabase = if ($TestFile -in @('tests/test_c06_authorization_postgres.py','tests/test_c06_lifecycle_postgres.py','tests/test_c06_security_postgres.py','tests/test_c06_recovery_postgres.py','tests/test_c06_handoff_schema_postgres.py','tests/test_c06_handoff_service_postgres.py','tests/test_joint_ask_postgres.py','tests/test_c06_result_store_postgres.py','tests/test_c06_conversations_postgres.py','tests/test_c06_sealing_postgres.py')) { 'c06_result_test' } else { 'auth_test_db' }
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$container = "ai-auth-test-$suffix"
$network = "ai-auth-network-$suffix"
$state = Join-Path $repoRoot ".local/auth-tests/$suffix"
Assert-TestState $repoRoot 'auth-tests' $state
New-Item -ItemType Directory -Path $state | Out-Null
if ($IsWindows) {
    $acl = Get-Acl -LiteralPath $state
    $acl.SetAccessRuleProtection($true, $false)
    $operatorSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    foreach ($sid in @($operatorSid, [Security.Principal.SecurityIdentifier]::new("S-1-5-18"))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $state -AclObject $acl
} else {
    & chmod 700 $state
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict private test-state permissions" }
}
# Unique throwaway secrets stay in ignored local files, never command arguments.
$password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
[IO.File]::WriteAllText((Join-Path $state "postgres-password"), $password)
$url = "postgresql+psycopg://auth_test_owner:${password}@${container}:5432/$testDatabase"
@{url=$url} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $state "admin.json")
$password = $null
$url = $null

function Docker-Checked {
    param([string[]]$Arguments)
    & $Docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker test infrastructure command failed" }
}

$containerAttempted = $false
$previous = Enter-IsolatedTestEnvironment
try {
    Docker-Checked @("network", "create", "--internal", "--label", "ai-platform-test-run=$suffix", $network)
    $containerAttempted = $true
    Docker-Checked @("run", "-d", "--name", $container, "--network", $network,
        "--label", "ai-platform-test-run=$suffix",
        "--mount", "type=bind,source=$state,target=/run/auth-test,readonly",
        "-e", "POSTGRES_USER=auth_test_owner", "-e", "POSTGRES_DB=$testDatabase",
        "-e", "POSTGRES_PASSWORD_FILE=/run/auth-test/postgres-password", $PostgresImage)
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        # The image's temporary initialization server accepts Unix sockets before
        # restarting. TCP is available only on the final server used by tests.
        & $Docker exec $container pg_isready -h 127.0.0.1 -U auth_test_owner -d $testDatabase *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "Isolated PostgreSQL did not become ready" }
    # Recovery artifacts BEFORE migrations or privilege changes, inside new cluster only.
    Docker-Checked @("exec", $container, "pg_dump", "-U", "auth_test_owner", "-d", $testDatabase, "-Fc", "-f", "/tmp/auth-before.dump")
    Docker-Checked @("exec", $container, "pg_dumpall", "-U", "auth_test_owner", "--globals-only", "--no-role-passwords", "-f", "/tmp/auth-globals-before.sql")
    Docker-Checked @("cp", "${container}:/tmp/auth-before.dump", (Join-Path $state "auth-before.dump"))
    Docker-Checked @("cp", "${container}:/tmp/auth-globals-before.sql", (Join-Path $state "auth-globals-before.sql"))
    # Verify archive readability; this is a fresh empty test database, not company data.
    Docker-Checked @("exec", $container, "pg_restore", "--list", "/tmp/auth-before.dump")
    $clientRuns = if ($RestoreDrill) { 2 } else { 1 }
    for ($clientRun = 0; $clientRun -lt $clientRuns; $clientRun++) {
        $clientFile = $TestFile; $clientReport = "results.xml"
        if ($clientRun -eq 1) {
            if ($testDatabase -ne 'c06_result_test') { throw 'The restore drill applies only to the C06 result store' }
            $restored = "c06_restored_$suffix"
            $runtimePassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
            $owner = "auth_test_owner"
            Docker-Checked @("exec", $container, "pg_dump", "-U", $owner, "-d", $testDatabase, "-Fc", "-f", "/tmp/c06-restore.dump")
            Docker-Checked @("exec", $container, "pg_restore", "--list", "/tmp/c06-restore.dump")
            Docker-Checked @("cp", "${container}:/tmp/c06-restore.dump", (Join-Path $state "c06-restore.dump"))
            Docker-Checked @("exec", $container, "createdb", "-U", $owner, $restored)
            Docker-Checked @("exec", $container, "psql", "-U", $owner, "-d", $restored, "-v", "ON_ERROR_STOP=1", "-c", "ALTER SCHEMA public OWNER TO c06_store_owner")
            Docker-Checked @("exec", $container, "pg_restore", "-U", $owner, "-d", $restored, "--single-transaction", "--exit-on-error", "--no-owner", "--role=c06_store_owner", "/tmp/c06-restore.dump")
            Docker-Checked @("exec", $container, "psql", "-U", $owner, "-d", $restored, "-v", "ON_ERROR_STOP=1", "-c", "REVOKE ALL ON DATABASE $restored FROM PUBLIC", "-c", "GRANT CONNECT ON DATABASE $restored TO c06_test_api")
            # The throwaway password goes through a private file (mounted into the database
            # container), never a command argument.
            [IO.File]::WriteAllText((Join-Path $state "restore-role.sql"), "ALTER ROLE c06_test_api PASSWORD '$runtimePassword';")
            Docker-Checked @("exec", $container, "psql", "-U", $owner, "-d", $restored, "-v", "ON_ERROR_STOP=1", "-q", "-f", "/run/auth-test/restore-role.sql")
            $source = (Get-Content -Raw (Join-Path $state "admin.json") | ConvertFrom-Json).url
            $restoredUrl = $source -replace "/$testDatabase$", "/$restored"
            $runtimeUrl = $restoredUrl -replace "auth_test_owner:[0-9a-f]+@", "c06_test_api:${runtimePassword}@"
            @{source_admin=$source; restored_admin=$restoredUrl; restored_runtime=$runtimeUrl; restored_database=$restored} |
                ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $state "restore-drill.json")
            $runtimePassword = $null; $source = $null; $restoredUrl = $null; $runtimeUrl = $null
            $clientFile = "tests/test_c06_restore_verify_postgres.py"; $clientReport = "restore-results.xml"
        }
        Docker-Checked @("run", "--rm", "--network", $network,
            "--user", $testUser, "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--mount", "type=bind,source=$backendRoot,target=/repo/Backend,readonly",
            "--mount", "type=bind,source=$(Join-Path $repoRoot 'pytest.ini'),target=/repo/pytest.ini,readonly",
            "--mount", "type=bind,source=$(Join-Path $repoRoot 'conftest.py'),target=/repo/conftest.py,readonly",
            "--workdir", "/repo/Backend",
            "--mount", "type=bind,source=$state,target=/reports",
            "-e", "APP_ENV=test", "-e", "AUTH_MODE=disabled", "-e", "PYTHON_DOTENV_DISABLED=1",
            "-e", "RUN_AUTH_POSTGRES_TESTS=1", "-e", "AUTH_TEST_BACKUP_ACK=1",
            "-e", "AUTH_TEST_ADMIN_URL_FILE=/reports/admin.json", "-e", "AUTH_TEST_REPORT_DIR=/reports", $Image,
            "python", "-m", "pytest", "-q", $clientFile, "--isolated-postgres", "--junitxml=/reports/$clientReport", "--tb=short", "-p", "no:cacheprovider")
    }
}
finally {
    # Preserve the stopped test container and recovery artifacts. Never delete a database.
    $stopFailed = $false
    try {
        Assert-TestState $repoRoot 'auth-tests' $state
        if ($containerAttempted) {
            $owner = & $Docker inspect --format '{{index .Config.Labels "ai-platform-test-run"}}' $container 2>$null
            if ($LASTEXITCODE -ne 0 -or $owner -ne $suffix) {
                # Ambiguous creation failure: never stop a resource without ownership proof.
                $stopFailed = $true
            } else {
                & $Docker stop $container *> $null
                if ($LASTEXITCODE -ne 0) { $stopFailed = $true }
                $running = & $Docker inspect --format '{{.State.Running}}' $container 2>$null
                if ($LASTEXITCODE -ne 0 -or $running -ne 'false') { $stopFailed = $true }
                if (-not $stopFailed) { Write-Host "Stopped isolated test container: $container" }
            }
        }
    } catch { $stopFailed = $true }
    finally { Restore-TestEnvironment $previous }
    Write-Host "Private recovery artifacts: $state"
    Write-Host "No ports published; existing development databases were not targeted."
    if ($stopFailed) { throw 'Could not confirm isolated Docker container shutdown' }
}
