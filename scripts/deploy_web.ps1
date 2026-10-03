# Deploy the page and its district bundles to the Hugging Face Static Space (free tier).
#
# The whole web/ folder goes up, not just index.html: the district bundles sit beside the
# page and are fetched as static files, which is what lets the map, the hover catchment
# and the shortlist work with no backend at all.
#
# Requires: pip install huggingface_hub, then `hf auth login` with your own token.
# No token is stored here - the hf CLI reads your own stored login.

param(
    [string]$Space = "arahmanmdmajid/rasta-school-access",
    [string]$Message = "Deploy Rasta web page and district bundles"
)

# Deliberately NOT "Stop": the hf CLI writes progress to stderr, and under Windows
# PowerShell 5.1 that is wrapped as a NativeCommandError which aborts an otherwise
# successful upload. Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$web  = Join-Path $root "web"

if (-not (Test-Path (Join-Path $web "index.html"))) { throw "web/index.html not found" }
$bundles = (Get-ChildItem (Join-Path $web "districts") -Filter "PK*.json" -ErrorAction SilentlyContinue).Count
if ($bundles -lt 1) { throw "no district bundles in web/districts - run scripts/build_bundles.py first" }
Write-Host "Deploying index.html + $bundles district bundles to $Space"

function Invoke-Hf {
    & hf @args
    if ($LASTEXITCODE -ne 0) { throw "hf $($args[0]) failed (exit $LASTEXITCODE)" }
}

Invoke-Hf repos create $Space --repo-type space --space-sdk static --exist-ok
Invoke-Hf upload $Space $web . --repo-type space --commit-message $Message

$slug = $Space.Replace("/", "-").ToLower()
Write-Host "Deployed: https://$slug.static.hf.space"
