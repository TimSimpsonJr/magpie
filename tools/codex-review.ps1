<#
.SYNOPSIS
  Run a Codex cross-model review with UTF-8 pinned end-to-end.

.DESCRIPTION
  On Windows PowerShell 5.1 the default file-read encoding (Get-Content) and the
  native-command pipe encoding are cp1252. Piping a UTF-8 review prompt into
  `codex` therefore mojibakes em-dashes and smart quotes. This helper:
    * reads the prompt via .NET ReadAllText as UTF-8 (never Get-Content), and
    * pins $OutputEncoding + [Console]::OutputEncoding to UTF-8 (no BOM)
  so the prompt reaches codex's stdin intact and codex's stdout decodes cleanly.

  It mirrors the manual cross-model-review flow used at phase boundaries while
  the build is on `main` (the cross-model-review plugin's native impl-review gate
  diffs a feature branch vs main and exits on the default branch):

      codex exec --sandbox read-only -C <repo>      (prompt supplied on stdin)

  Codex's stdout (the review) is written to -OutFile; its stderr (verbose
  progress, large -- ignore) flows through to the caller's stderr, which the
  Claude Code PowerShell tool captures separately.

.PARAMETER PromptPath
  Path to a UTF-8 prompt file. Assemble it with the Write tool (writes UTF-8) or
  any UTF-8 writer -- NOT Get-Content round-trips.

.PARAMETER Prompt
  Inline prompt string (alternative to -PromptPath).

.PARAMETER Repo
  Repository root Codex reviews (codex -C). Defaults to this repo (the parent of
  tools/).

.PARAMETER OutFile
  Where Codex's stdout (the review) is written, UTF-8 no BOM. Defaults to
  .codex-review/last-review.md under the repo (gitignored).

.PARAMETER Sandbox
  Codex sandbox policy. Default read-only -- a review must never write.

.EXAMPLE
  # Claude writes the prompt file via the Write tool, then:
  ./tools/codex-review.ps1 -PromptPath .codex-review/phase4-prompt.md
  # ... then Read .codex-review/last-review.md for the verdict.
#>
[CmdletBinding(DefaultParameterSetName = 'File')]
param(
    [Parameter(ParameterSetName = 'File', Mandatory)][string]$PromptPath,
    [Parameter(ParameterSetName = 'Inline', Mandatory)][string]$Prompt,
    [string]$Repo,
    [string]$OutFile,
    [ValidateSet('read-only', 'workspace-write', 'danger-full-access')][string]$Sandbox = 'read-only'
)

# Resolve repo root (default: the directory above tools/).
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
$Repo = (Resolve-Path -LiteralPath $Repo).Path

$utf8 = [System.Text.UTF8Encoding]::new($false)   # UTF-8, no BOM

# Read the prompt as UTF-8 (Get-Content would mangle non-ASCII on PS 5.1).
if ($PSCmdlet.ParameterSetName -eq 'File') {
    $PromptPath = (Resolve-Path -LiteralPath $PromptPath).Path
    $Prompt = [System.IO.File]::ReadAllText($PromptPath, $utf8)
}
if ([string]::IsNullOrWhiteSpace($Prompt)) { throw "Prompt is empty." }

if (-not $OutFile) { $OutFile = Join-Path $Repo '.codex-review\last-review.md' }
$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

# Pin pipe + console to UTF-8 so the prompt reaches codex intact and its stdout
# decodes cleanly. Restore afterward so we don't perturb the rest of the shell.
$prevOut = $OutputEncoding
$prevConsoleOut = [Console]::OutputEncoding
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
try {
    Write-Host "codex exec --sandbox $Sandbox -C `"$Repo`"  (prompt: $($Prompt.Length) chars)"
    # Prompt on stdin only (no positional arg), so codex reads it as the
    # instructions. stderr is left to flow to the caller (don't 2>&1 a native
    # command on PS 5.1 -- it wraps lines as ErrorRecords).
    $review = $Prompt | & codex exec --sandbox $Sandbox -C $Repo
    [System.IO.File]::WriteAllText($OutFile, (($review -join "`n") + "`n"), $utf8)
    Write-Host "Review written to: $OutFile"
}
finally {
    $OutputEncoding = $prevOut
    [Console]::OutputEncoding = $prevConsoleOut
}
