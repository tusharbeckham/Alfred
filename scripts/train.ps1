# Alfred — Training loop. Runs ONE eval-driven self-improvement iteration.
# Usage: powershell -File scripts\train.ps1 [-Suite coding|qa|all]
# NOTE: optimizes prompts/skills only. It does NOT train model weights.
param([string]$Suite = "all")
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Alfred'
$directive = "Run ONE eval-driven training iteration on the '$Suite' suite per " +
  "prompts/training/train-loop.txt. Evaluate with evals/, score with evals/rubric.json, " +
  "diagnose the weakest category, have alfred-prompt-engineer propose a MINIMAL improvement, " +
  "regression-test the FULL suite, accept only if nothing regresses, save the version under " +
  "training/prompt-versions/, and append the delta + decision to training/history.md. " +
  "Do NOT train model weights and do NOT use Kaggle."
Write-Host "[train] start $(Get-Date -Format o) suite=$Suite"
kiro-cli chat --no-interactive --trust-all-tools --agent alfred-trainer $directive
Write-Host "[train] end   $(Get-Date -Format o) exit=$LASTEXITCODE"
