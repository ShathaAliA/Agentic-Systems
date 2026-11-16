"""Execute safety helpers with synthetic inputs; never launch Docker/PostgreSQL."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_test_execution import runner

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Backend/tools"
PWSH = shutil.which("pwsh")


def ps(tmp_path, body, *, env=None):
    if not PWSH:
        pytest.skip("PowerShell unavailable; helper execution requires PowerShell 7.2+")
    path = tmp_path / "probe.ps1"
    helper = str(TOOLS / "test-isolation.ps1").replace("'", "''")
    path.write_text("$ErrorActionPreference='Stop'\n. '" + helper + "'\n" + body, encoding="utf-8")
    return subprocess.run([PWSH, "-NoProfile", "-File", str(path)],
                          env=env or runner.clean_environment(os.environ),
                          capture_output=True, text=True, timeout=30)


def test_python_and_powershell_share_environment_policy_and_restore(tmp_path):
    names = ["AUTH_SESSION_SECONDS", "AUTH_TEST_ADMIN_URL_FILE", "DATABASE_URL", "PGSERVICEFILE",
             "OPENAI_API_KEY", "TENANT_CONTRACT_FILE", "LOCAL_EMBEDDING_URL",
             "OTHER_PASSWORD", "RUN_DEVELOPMENT_PROVISIONING", "DEPARTMENT_CREDENTIALS_DIR"]
    env = runner.clean_environment(os.environ)
    env.update({name: "synthetic-canary" for name in names})
    result = ps(tmp_path, """
$saved = Enter-IsolatedTestEnvironment
try {
    $present = @([Environment]::GetEnvironmentVariables('Process').Values | Where-Object { $_ -eq 'synthetic-canary' }).Count
    if ($present -ne 0 -or $env:AUTH_MODE -ne 'disabled' -or $env:PYTHON_DOTENV_DISABLED -ne '1') { throw 'Environment not isolated' }
    $env:AUTH_TEST_GENERATED = 'generated'
} finally { Restore-TestEnvironment $saved }
if ($env:AUTH_TEST_GENERATED) { throw 'Generated credential reference survived restore' }
if ($env:DATABASE_URL -ne 'synthetic-canary') { throw 'Caller environment not restored' }
Write-Output 'passed'
""", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"
    assert "synthetic-canary" not in runner.clean_environment(env).values()


@pytest.mark.parametrize("endpoint,allowed", [("unix:///var/run/docker.sock", True),
    ("npipe:////./pipe/docker_engine", True), ("tcp://127.0.0.1:2375", False),
    ("ssh://remote", False), ("tcp://remote:2376", False)])
def test_docker_endpoint_is_verified_without_contacting_engine(tmp_path, endpoint, allowed):
    result = ps(tmp_path, f"""
$env:DOCKER_HOST = '{endpoint}'
$env:DOCKER_CONTEXT = $null
function NoDocker {{ throw 'Engine must not be called' }}
try {{ Assert-LocalTestDocker 'NoDocker'; Write-Output 'allowed' }}
catch {{ Write-Output 'denied' }}
""")
    assert result.returncode == 0
    assert result.stdout.strip() == ("allowed" if allowed else "denied")


def test_docker_context_precedence_cannot_hide_remote_engine(tmp_path):
    result = ps(tmp_path, """
$env:DOCKER_HOST = 'unix:///var/run/docker.sock'
$env:DOCKER_CONTEXT = 'synthetic-remote'
function MockContext { $global:LASTEXITCODE=0; return 'ssh://synthetic-remote' }
try { Assert-LocalTestDocker 'MockContext'; throw 'Unsafe context accepted' }
catch { if ($_.Exception.Message -ne 'Only a local Docker socket is allowed for tests') { throw }; Write-Output 'passed' }
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"


def test_client_uid_never_falls_back_to_root(tmp_path):
    result = ps(tmp_path, """
if ($IsWindows) {
    if ((Get-TestClientUser) -ne '10001:10001') { throw 'Unexpected Windows test UID' }
} else {
    function id { $global:LASTEXITCODE=0; return '0' }
    $denied = $false
    try { Get-TestClientUser } catch { $denied = $true }
    if (-not $denied) { throw 'Root operator accepted' }
}
Write-Output 'passed'
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"


def test_test_state_rejects_existing_database_paths_and_traversal(tmp_path):
    repo = str(tmp_path).replace("'", "''")
    result = ps(tmp_path, f"""
$repo = '{repo}'
$valid = Join-Path $repo '.local/native-auth-tests/0123456789abcdef0123456789abcdef'
Assert-TestState $repo 'native-auth-tests' $valid
$bad = @((Join-Path $repo '.local/native-auth-tests/../existing'),
         (Join-Path $repo '.local/native-auth-tests/hr'), (Join-Path $repo 'data'))
foreach ($path in $bad) {{
    $rejected = $false
    try {{ Assert-TestState $repo 'native-auth-tests' $path }} catch {{ $rejected = $true }}
    if (-not $rejected) {{ throw 'Unsafe state accepted' }}
}}
Write-Output 'passed'
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "passed"


def test_missing_executable_fails_with_safe_preflight_error(tmp_path):
    result = ps(tmp_path, """
try { Resolve-TestExecutable 'missing-test-executable-synthetic-canary'; throw 'Unexpected success' }
catch { if ($_.Exception.Message -ne 'Required test executable is unavailable') { throw }; Write-Output 'passed' }
""")
    assert result.returncode == 0, result.stderr
    assert "synthetic-canary" not in result.stdout + result.stderr


def test_configured_powershell_is_used_for_private_report_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    commands = []
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: commands.append(command))
    runner.restrict_directory(tmp_path / "report", pwsh="selected-powershell")
    assert commands[0][0] == "selected-powershell"
    assert "restrict-test-directory.ps1" in commands[0][3]


def test_report_preflight_failure_spawns_no_tests(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    def denied(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "synthetic", stderr="private-canary")
    monkeypatch.setattr(runner, "restrict_directory", denied)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("Test was spawned"))
    assert runner.run(SimpleNamespace(pwsh="selected")) == 1
    assert "private-canary" not in capsys.readouterr().out


@pytest.mark.parametrize("owned", [True, False])
def test_docker_cleanup_requires_ownership_and_confirms_stop(tmp_path, owned):
    repo = str(tmp_path).replace("'", "''")
    script = str(TOOLS / "test-auth-postgres.ps1").replace("'", "''")
    result = ps(tmp_path, f"""
$repoRoot = '{repo}'
$state = Join-Path $repoRoot '.local/auth-tests/0123456789ab'
$suffix = '0123456789ab'
$container = 'ai-auth-test-' + $suffix
$containerAttempted = $true
$previous = @{{}}
$script:stops = 0
function MockDocker {{
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'stop') {{ $script:stops++; return }}
    if ($args[2] -like '*Labels*') {{ return '{'0123456789ab' if owned else 'another-owner'}' }}
    return 'false'
}}
$Docker = 'MockDocker'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$null)
$main = @($ast.EndBlock.Statements | Where-Object {{ $_ -is [System.Management.Automation.Language.TryStatementAst] }})[-1]
$failed = $false
try {{ & ([scriptblock]::Create('& ' + $main.Finally.Extent.Text)) }} catch {{ $failed = $true }}
@{{stops=$script:stops;failed=$failed}} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout.splitlines()[-1])
    assert data == {"stops": 1 if owned else 0, "failed": not owned}


def test_docker_client_command_is_non_root_and_capability_restricted(tmp_path):
    script = str(TOOLS / "test-auth-postgres.ps1").replace("'", "''")
    result = ps(tmp_path, f"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$null)
$commands = $ast.FindAll({{param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Docker-Checked'}}, $true)
$client = @($commands | Where-Object {{ $_.Extent.Text -match '"--rm"' }})
if ($client.Count -ne 1) {{ throw 'Missing client command' }}
$testUser = '10001:10001'; $network = 'synthetic'; $backendRoot = 'synthetic'; $repoRoot = 'synthetic'
$state = 'synthetic'; $Image = 'synthetic'; $TestFile = 'synthetic'
function Docker-Checked {{ param([string[]]$Arguments) ConvertTo-Json -InputObject $Arguments -Compress }}
& ([scriptblock]::Create($client[0].Extent.Text))
""")
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    for option, value in [("--user", "10001:10001"), ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges:true")]:
        assert args[args.index(option) + 1] == value
    docker_args = args[:args.index("python")]
    assert "--privileged" not in docker_args and "-p" not in docker_args
