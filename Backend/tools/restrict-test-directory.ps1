#requires -Version 7.2
param([Parameter(Mandatory)][string]$Path)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $Path | Out-Null
$acl = Get-Acl -LiteralPath $Path
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,
                  [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $Path -AclObject $acl
