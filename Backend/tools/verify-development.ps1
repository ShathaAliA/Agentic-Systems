param([string]$Container='ai_platform_postgres')
$ErrorActionPreference='Stop'
$repoPath=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dockerExe=(Get-Command docker -ErrorAction SilentlyContinue).Source
if(-not $dockerExe){$dockerExe='C:\Users\Do0oz\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'}
function Check-Native { if($LASTEXITCODE -ne 0){throw 'Development command failed; stop and inspect before continuing'} }
$metadata=(& $dockerExe inspect $Container --format '{{json .}}' | ConvertFrom-Json)
Check-Native
if($Container -ne 'ai_platform_postgres' -or $metadata.Config.Image -ne 'postgres:17-alpine'){throw 'Unexpected development target'}
if($metadata.HostConfig.PortBindings.'5432/tcp'[0].HostIp -ne '127.0.0.1'){throw 'Expected loopback-only development PostgreSQL'}
$demoCount=& $dockerExe exec $Container psql -U ai_user -d ai_platform_db -At -c "SELECT count(*) FROM users WHERE email IN ('admin@example.com','hr.manager@example.com','finance.employee@example.com')"
Check-Native
if($demoCount.Trim() -ne '3'){throw 'Development seed identity check failed'}
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath=Join-Path $repoPath ".local/backups/$stamp"
New-Item -ItemType Directory -Force $backupPath | Out-Null
@{image=$metadata.Config.Image;ports=$metadata.HostConfig.PortBindings;composeProject=$metadata.Config.Labels.'com.docker.compose.project'}|ConvertTo-Json -Depth 5|Set-Content (Join-Path $backupPath 'development-target.json') -Encoding utf8
& $dockerExe exec $Container psql -U ai_user -d postgres -At -c "SELECT json_agg(s) FROM (SELECT datname,pg_get_userbyid(datdba) AS owner,datacl::text AS acl FROM pg_database ORDER BY datname) s" | Set-Content (Join-Path $backupPath 'owners-acls.json') -Encoding utf8
Check-Native
foreach($database in @('hr_db','finance_db')){
    $inside="/tmp/day1-$database-$stamp.dump"
    & $dockerExe exec $Container pg_dump -U ai_user -d $database -Fc -f $inside
    Check-Native
    & $dockerExe exec $Container pg_restore --list $inside | Set-Content (Join-Path $backupPath "$database-toc.txt") -Encoding utf8
    Check-Native
    & $dockerExe cp "${Container}:$inside" (Join-Path $backupPath "$database.dump")
    Check-Native
}
Get-FileHash -LiteralPath (Join-Path $backupPath 'hr_db.dump'),(Join-Path $backupPath 'finance_db.dump')|Select-Object Path,Hash|ConvertTo-Json|Set-Content (Join-Path $backupPath 'sha256.json') -Encoding utf8
$password=& $dockerExe exec $Container printenv POSTGRES_PASSWORD
Check-Native
$url='postgresql+psycopg://ai_user:'+([Uri]::EscapeDataString($password))+'@ai_platform_postgres:5432/ai_platform_db'
[IO.File]::WriteAllText((Join-Path $repoPath '.local/dev-admin.json'),(@{url=$url}|ConvertTo-Json -Compress))
Remove-Variable password,url,metadata
& $dockerExe build -t ai-platform-day1:dev (Join-Path $repoPath 'Backend')
Check-Native
& $dockerExe volume create ai_platform_day1_dev_state | Out-Null
Check-Native
& $dockerExe run --rm --user 0 --network none --mount type=volume,source=ai_platform_day1_dev_state,target=/state ai-platform-day1:dev chown 10001:10001 /state
Check-Native
$network=(& $dockerExe inspect $Container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}').Trim().Split(' ')[0]
& $dockerExe run --rm --name day1-development-check --network $network --mount "type=bind,source=$repoPath/Backend,target=/backend,readonly" --mount "type=bind,source=$repoPath/.local/dev-admin.json,target=/run/dev-admin.json,readonly" --mount type=volume,source=ai_platform_day1_dev_state,target=/state -e RUN_DEVELOPMENT_PROVISIONING=1 -e DEV_BACKUP_ACK=1 -e DEV_ADMIN_URL_FILE=/run/dev-admin.json -e DEV_STATE_DIR=/state/day1 ai-platform-day1:dev python -m pytest -q -s tests/test_development_provisioning.py --allow-live-provisioning --tb=short -p no:cacheprovider
Check-Native
Write-Output "Development verification passed; backups at $backupPath. No databases deleted."
