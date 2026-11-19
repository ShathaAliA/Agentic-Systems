#requires -Version 7.2
# Functions only. Dot-sourcing does not execute infrastructure commands.
$script:TestEnvironmentPolicy = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'test-environment-policy.json') -Raw | ConvertFrom-Json

function Test-IsPrivateTestVariable([string]$Name) {
    $key = $Name.ToUpperInvariant()
    if ($script:TestEnvironmentPolicy.names -contains $key) { return $true }
    foreach ($prefix in $script:TestEnvironmentPolicy.prefixes) { if ($key.StartsWith($prefix)) { return $true } }
    foreach ($suffix in $script:TestEnvironmentPolicy.suffixes) { if ($key.EndsWith($suffix)) { return $true } }
    return $false
}

function Enter-IsolatedTestEnvironment {
    $saved = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        if ((Test-IsPrivateTestVariable $entry.Key) -or $entry.Key -eq 'PYTHONIOENCODING') {
            $saved[$entry.Key] = $entry.Value
            [Environment]::SetEnvironmentVariable($entry.Key, $null, 'Process')
        }
    }
    foreach ($entry in $script:TestEnvironmentPolicy.defaults.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, 'Process')
    }
    return $saved
}

function Restore-TestEnvironment([hashtable]$Saved) {
    foreach ($key in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
        if ((Test-IsPrivateTestVariable $key) -or $key -eq 'PYTHONIOENCODING') {
            [Environment]::SetEnvironmentVariable($key, $null, 'Process')
        }
    }
    foreach ($entry in $Saved.GetEnumerator()) { [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process') }
}

function Assert-TestState([string]$Repo, [string]$Area, [string]$State) {
    if ($Area -notin @('native-auth-tests', 'auth-tests')) { throw 'Invalid test-state area' }
    $parent = [IO.Path]::GetFullPath((Join-Path $Repo ".local/$Area"))
    $full = [IO.Path]::GetFullPath($State)
    $comparison = if ($IsWindows) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
    if (-not [IO.Path]::GetDirectoryName($full).Equals($parent, $comparison) -or [IO.Path]::GetFileName($full) -notmatch '^[0-9a-f]{12}([0-9a-f]{20})?$') {
        throw 'Test state is outside the owned test directory'
    }
    $cursor = $full
    while ($cursor -and -not $cursor.Equals([IO.Path]::GetFullPath($Repo), $comparison)) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Linked test-state paths are forbidden'
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Resolve-TestExecutable([string]$Executable) {
    try {
        # Linux PATH may resolve both /usr/bin and its /bin symlink. Return one
        # application path, never an array coerced into a space-separated name.
        $application = Get-Command -Name $Executable -CommandType Application -ErrorAction Stop | Select-Object -First 1
        if (-not $application.Source) { throw 'Missing executable source' }
        return $application.Source
    }
    catch { throw 'Required test executable is unavailable' }
}

function Assert-LocalTestDocker([string]$Docker) {
    $endpoint = $null
    # DOCKER_CONTEXT takes precedence over DOCKER_HOST in the Docker CLI.
    if ($env:DOCKER_CONTEXT) {
        $endpoint = & $Docker context inspect $env:DOCKER_CONTEXT --format '{{.Endpoints.docker.Host}}' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot verify local Docker context' }
    } else { $endpoint = $env:DOCKER_HOST }
    if (-not $endpoint) {
        $endpoint = & $Docker context inspect --format '{{.Endpoints.docker.Host}}' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot verify local Docker context' }
    }
    if ($endpoint -notmatch '^(npipe|unix)://') { throw 'Only a local Docker socket is allowed for tests' }
}

function Get-TestClientUser {
    if ($IsWindows) { return '10001:10001' }
    $testUid = (& id -u).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve test operator UID' }
    $testGid = (& id -g).Trim()
    if ($LASTEXITCODE -ne 0 -or $testUid -notmatch '^[1-9][0-9]*$' -or $testGid -notmatch '^[0-9]+$') {
        throw 'A non-root operator is required; private directory permissions must not be weakened'
    }
    return "${testUid}:${testGid}"
}
