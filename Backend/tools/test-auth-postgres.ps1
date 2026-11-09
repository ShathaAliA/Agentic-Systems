#requires -Version 7.2
param(
    [string]$Docker = "docker",
    [string]$Image = "ai-platform-core:test",
    [ValidateSet("tests/test_tenant_contract_postgres.py", "tests/test_database_isolation_postgres.py", "tests/test_core_lifecycle_postgres.py", "tests/test_authentication_postgres.py", "tests/test_departments_postgres.py", "tests/test_users_postgres.py", "tests/test_usage_postgres.py", "tests/test_role_policies_postgres.py", "tests/test_core_access_postgres.py")]
    [string]$TestFile = "tests/test_authentication_postgres.py"
)
$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$backendRoot = Join-Path $repoRoot "Backend"
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$container = "ai-auth-test-$suffix"
$network = "ai-auth-network-$suffix"
$state = Join-Path $repoRoot ".local/auth-tests/$suffix"
New-Item -ItemType Directory -Path $state -Force | Out-Null
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
$url = "postgresql+psycopg://auth_test_owner:${password}@${container}:5432/auth_test_db"
@{url=$url} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $state "admin.json")
$password = $null
$url = $null

function Docker-Checked {
    param([string[]]$Arguments)
    & $Docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker test infrastructure command failed" }
}

try {
    Docker-Checked @("network", "create", "--internal", $network)
    Docker-Checked @("run", "-d", "--name", $container, "--network", $network,
        "--mount", "type=bind,source=$state,target=/run/auth-test,readonly",
        "-e", "POSTGRES_USER=auth_test_owner", "-e", "POSTGRES_DB=auth_test_db",
        "-e", "POSTGRES_PASSWORD_FILE=/run/auth-test/postgres-password", "postgres:17-alpine")
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        # The image's temporary initialization server accepts Unix sockets before
        # restarting. TCP is available only on the final server used by tests.
        & $Docker exec $container pg_isready -h 127.0.0.1 -U auth_test_owner -d auth_test_db *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "Isolated PostgreSQL did not become ready" }
    # Recovery artifacts BEFORE migrations or privilege changes, inside new cluster only.
    Docker-Checked @("exec", $container, "pg_dump", "-U", "auth_test_owner", "-d", "auth_test_db", "-Fc", "-f", "/tmp/auth-before.dump")
    Docker-Checked @("exec", $container, "pg_dumpall", "-U", "auth_test_owner", "--globals-only", "--no-role-passwords", "-f", "/tmp/auth-globals-before.sql")
    Docker-Checked @("cp", "${container}:/tmp/auth-before.dump", (Join-Path $state "auth-before.dump"))
    Docker-Checked @("cp", "${container}:/tmp/auth-globals-before.sql", (Join-Path $state "auth-globals-before.sql"))
    # Verify archive readability; this is a fresh empty test database, not company data.
    Docker-Checked @("exec", $container, "pg_restore", "--list", "/tmp/auth-before.dump")
    Docker-Checked @("run", "--rm", "--network", $network,
        "--mount", "type=bind,source=$backendRoot,target=/repo/Backend,readonly",
        "--mount", "type=bind,source=$(Join-Path $repoRoot 'pytest.ini'),target=/repo/pytest.ini,readonly",
        "--mount", "type=bind,source=$(Join-Path $repoRoot 'conftest.py'),target=/repo/conftest.py,readonly",
        "--workdir", "/repo/Backend",
        "--mount", "type=bind,source=$state,target=/reports",
        "-e", "RUN_AUTH_POSTGRES_TESTS=1", "-e", "AUTH_TEST_BACKUP_ACK=1",
        "-e", "AUTH_TEST_ADMIN_URL_FILE=/reports/admin.json", $Image,
        "python", "-m", "pytest", "-q", $TestFile, "--isolated-postgres", "--junitxml=/reports/results.xml", "--tb=short", "-p", "no:cacheprovider")
}
finally {
    # Preserve the stopped test container and recovery artifacts. Never delete a database.
    & $Docker stop $container *> $null
    $stopFailed = $LASTEXITCODE -ne 0
    Write-Host "Stopped isolated test container: $container"
    Write-Host "Private recovery artifacts: $state"
    Write-Host "No ports published; existing development databases were not targeted."
    if ($stopFailed) { throw 'Could not confirm isolated Docker container shutdown' }
}
