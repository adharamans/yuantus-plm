#requires -Version 5.1
<#
.SYNOPSIS
    Stage PRE-BUILT CAD helper artifacts and compile the per-user Inno
    installer. This is the PACK phase only -- it does NOT build, and it
    does NOT invoke MSBuild / dotnet for the consumed projects (taskbook
    §3.C / guard 10). The BUILD phase is a separate, prior step that leaves
    output under each project's bin/... :

      dotnet publish Helper   -c Release -r win-x64 --self-contained true
      dotnet publish Detector -c Release -r win-x64 --self-contained true
      dotnet build   Bridge   -c Release
      msbuild CADDedupPlugin.csproj /p:Configuration=Release /p:Platform=x64 /p:AutoCADVersion=2018

.DESCRIPTION
    Staging layout (matches the .iss [Files] DestDir tree):
      staging\helper\          <- helper + detector publish output (flat)
      staging\cad-bridge\      <- Bridge net46 output (flat) + the .lsp
      staging\CADDedup.bundle\ <- assembled bundle (PackageContents.xml + Contents\)

    Signing is owner-local and FIRST-PARTY ONLY (taskbook §3.B): when
    -SignToolCmd is supplied, the first-party .exe/.dll in staging are
    Authenticode-signed here, BEFORE iscc packs them. Inno then signs the
    installer + uninstaller via the .iss [Setup] SignTool (also guarded by
    /DSignToolCmd). Third-party DLLs (Newtonsoft.Json.dll, the .NET runtime
    DLLs in a self-contained publish) are NOT re-signed -- owner policy.
    Omit -SignToolCmd (the CI default) for a fully UNSIGNED build.

.PARAMETER RepoRoot
    Repository root. Defaults to four levels up from this script.

.PARAMETER AutoCADVersion
    CADDedup bundle target year (selects PackageContents.<ver>.xml and the
    bin\x64\Release\AutoCAD<ver>\ output dir). Default 2018 (the baseline).

.PARAMETER SignToolCmd
    Full signtool invocation with a literal `$f` file placeholder, e.g.
    'signtool sign /fd sha256 /a /n "My Org" $f'. Owner-local only.

.EXAMPLE
    pwsh ./pack.ps1                                            # unsigned (CI)
.EXAMPLE
    pwsh ./pack.ps1 -SignToolCmd 'signtool sign /fd sha256 /a /n "My Org" $f'
#>
param(
    [string]$RepoRoot,
    [string]$AutoCADVersion = '2018',
    [string]$SignToolCmd
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..\..')).Path
}

$cdh = Join-Path $RepoRoot 'clients/cad-desktop-helper'
# Self-contained publish output (BUILD phase produces these with -r win-x64).
$helperPublish   = Join-Path $cdh 'Helper/bin/Release/net6.0-windows/win-x64/publish'
$detectorPublish = Join-Path $cdh 'Detector/bin/Release/net6.0-windows/win-x64/publish'
# Bridge is a net46 class library -> output lives under the net46 TFM folder.
$bridgeOut       = Join-Path $cdh 'Bridge/bin/Release/net46'
$lspFile         = Join-Path $cdh 'Lisp/yuantus_cad_helper.lsp'
# CADDedup raw output dir (TargetDir): bin\x64\Release\AutoCAD<ver>\
$pluginDir       = Join-Path $RepoRoot 'clients/autocad-material-sync/CADDedupPlugin'
$bundleBin       = Join-Path $pluginDir ("bin/x64/Release/AutoCAD{0}" -f $AutoCADVersion)
$pkgContents     = Join-Path $pluginDir ("PackageContents.{0}.xml" -f $AutoCADVersion)
if (-not (Test-Path $pkgContents)) { $pkgContents = Join-Path $pluginDir 'PackageContents.xml' }

$staging = Join-Path $here 'staging'
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
$null = New-Item -ItemType Directory -Path $staging

function New-Dir([string]$p) { $null = New-Item -ItemType Directory -Path $p -Force; return $p }

function Copy-Flat([string]$src, [string]$destDir) {
    # Copy ONLY -- no build. Pre-built output is the input. Flat (no TFM/RID
    # subdir) so binaries land at the destination root the .iss expects.
    # FAIL-FAST: a release pack must not silently ship a missing input.
    if (-not (Test-Path $src)) {
        throw "Required pre-built artifact missing: $src (run the BUILD phase first)"
    }
    Copy-Item (Join-Path $src '*') $destDir -Recurse -Force
}

function Require-File([string]$path, [string]$what) {
    if (-not (Test-Path $path)) { throw "Required $what missing: $path" }
}

# 1. helper + detector -> staging\helper\
$helperStage = New-Dir (Join-Path $staging 'helper')
Copy-Flat $helperPublish $helperStage
Copy-Flat $detectorPublish $helperStage

# 2. bridge (net46, FLAT) + lsp -> staging\cad-bridge\
$bridgeStage = New-Dir (Join-Path $staging 'cad-bridge')
Copy-Flat $bridgeOut $bridgeStage
Require-File $lspFile 'Lisp shell (yuantus_cad_helper.lsp)'
Copy-Item $lspFile $bridgeStage -Force

# 3. assemble CADDedup.bundle -> staging\CADDedup.bundle\ (replicate PostBuild)
$bundleStage = New-Dir (Join-Path $staging 'CADDedup.bundle')
$bundleContents = New-Dir (Join-Path $bundleStage 'Contents')
foreach ($dll in @('CADDedupPlugin.dll', 'Yuantus.Cad.Shared.dll', 'Newtonsoft.Json.dll')) {
    $f = Join-Path $bundleBin $dll
    Require-File $f "CADDedup bundle input ($dll)"
    Copy-Item $f $bundleContents -Force
}
Require-File $pkgContents 'CADDedup PackageContents xml'
Copy-Item $pkgContents (Join-Path $bundleStage 'PackageContents.xml') -Force

# 3b. FAIL-FAST: verify the actual required deliverables landed in staging
# (a publish dir can exist yet lack the expected binary).
Require-File (Join-Path $helperStage 'yuantus-cad-helper.exe')   'staged helper exe'
Require-File (Join-Path $helperStage 'yuantus-cad-detector.exe') 'staged detector exe'
Require-File (Join-Path $bridgeStage 'YuantusCadHelperBridge.dll') 'staged bridge DLL'
Require-File (Join-Path $bridgeStage 'yuantus_cad_helper.lsp')    'staged Lisp shell'
Require-File (Join-Path $bundleContents 'CADDedupPlugin.dll')     'staged CADDedup plugin DLL'

# 4. FIRST-PARTY-ONLY signing (owner-local). Third-party DLLs are not re-signed.
if ($SignToolCmd) {
    Write-Host 'Signing first-party payload (owner-local).'
    $firstParty = @(
        (Join-Path $helperStage 'yuantus-cad-helper.exe'),
        (Join-Path $helperStage 'yuantus-cad-detector.exe'),
        (Join-Path $helperStage 'Yuantus.Cad.Shared.dll'),
        (Join-Path $bridgeStage 'YuantusCadHelperBridge.dll'),
        (Join-Path $bundleContents 'CADDedupPlugin.dll'),
        (Join-Path $bundleContents 'Yuantus.Cad.Shared.dll')
    )
    foreach ($file in $firstParty) {
        # FAIL-FAST: a signed release must not skip a required first-party binary.
        Require-File $file 'first-party binary to sign'
        $cmd = $SignToolCmd.Replace('$f', ('"{0}"' -f $file))
        Write-Host "  sign: $file"
        & cmd.exe /c $cmd
        if ($LASTEXITCODE -ne 0) { throw "signing failed for $file (exit $LASTEXITCODE)" }
    }
} else {
    Write-Host 'Signing DISABLED -- UNSIGNED payload + installer (CI default).'
}

# 5. Compile the installer with iscc (no MSBuild here).
$iss = Join-Path $here 'YuantusCadHelper.iss'
$isccArgs = @("/DStagingDir=$staging")
if ($SignToolCmd) { $isccArgs += "/DSignToolCmd=$SignToolCmd" }
$isccArgs += $iss

Write-Host "iscc $($isccArgs -join ' ')"
& iscc @isccArgs
