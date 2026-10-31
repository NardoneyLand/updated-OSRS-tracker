# find-python.ps1 — shared helper, dot-sourced by build.ps1 and launch.ps1.
#
# Find-Python returns @{ Exe = ...; Args = @(...) } for the first interpreter
# that is Python 3.10+, or $null if none is found.

function Test-PythonCandidate {
    param([string]$Exe, [string[]]$PyArgs)
    # Native stderr must not become a terminating error under
    # $ErrorActionPreference = 'Stop' (Windows PowerShell 5.1).
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @PyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $old
    }
}

function Find-Python {
    if (Get-Command 'py' -ErrorAction SilentlyContinue) {
        foreach ($v in '-3.12', '-3.11', '-3.10', '-3') {
            if (Test-PythonCandidate 'py' @($v)) {
                return @{ Exe = 'py'; Args = @($v) }
            }
        }
    }
    if (Get-Command 'python' -ErrorAction SilentlyContinue) {
        if (Test-PythonCandidate 'python' @()) {
            return @{ Exe = 'python'; Args = @() }
        }
    }
    return $null
}
