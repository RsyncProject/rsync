param([switch]$OpenSSH)

$ErrorActionPreference = 'Stop'
if ($Env:GITHUB_ACTIONS -ne 'true' -or $Env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Cygwin installation is restricted to disposable hosted runners'
}
$setup = Join-Path $Env:RUNNER_TEMP 'setup-x86_64.exe'
$sums = Join-Path $Env:RUNNER_TEMP 'cygwin-sha512.sum'
$packages = 'make,autoconf,automake,gcc-core,attr,libattr-devel,python39,python39-pip,libzstd-devel,liblz4-devel,libssl-devel,libxxhash0,libxxhash-devel,libidn2-devel'
if ($OpenSSH) { $packages += ',openssh,openssl' }

Invoke-WebRequest https://cygwin.com/setup-x86_64.exe -OutFile $setup
Invoke-WebRequest https://cygwin.com/sha512.sum -OutFile $sums
$sum = Select-String -LiteralPath $sums -Pattern '^[0-9a-fA-F]{128}\s+\*?setup-x86_64\.exe$' | Select-Object -First 1
if (-not $sum) { throw 'setup-x86_64.exe is missing from Cygwin sha512.sum' }
$expected = ($sum.Line -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath $setup -Algorithm SHA512).Hash
if ($actual -ine $expected) { throw 'Cygwin setup SHA-512 mismatch' }

$arguments = @(
    '--quiet-mode', '--no-desktop', '--no-startmenu', '--no-shortcuts',
    '--root', 'C:\tools\cygwin',
    '--local-package-dir', (Join-Path $Env:RUNNER_TEMP 'cygwin-packages'),
    '--site', 'https://mirrors.kernel.org/sourceware/cygwin/',
    '--packages', $packages
)
$install = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
if ($install.ExitCode -ne 0) { exit $install.ExitCode }
$bash = 'C:\tools\cygwin\bin\bash.exe'
if (-not (Test-Path -LiteralPath $bash)) { throw 'Cygwin setup did not install bash' }
& $bash -lc 'command -v make aclocal gcc python3 >/dev/null'
if ($LASTEXITCODE -ne 0) { throw 'Cygwin setup did not install all required build tools' }
'C:/tools/cygwin/bin' >>$Env:GITHUB_PATH
