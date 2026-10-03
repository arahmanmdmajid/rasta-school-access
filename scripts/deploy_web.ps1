# Deploy the web page to the live Hugging Face Static Space (free).
# Requires: pip install huggingface_hub, then `hf auth login` with your own token.
param(
    [string]$Space = "arahmanmdmajid/rasta-school-access",
    [string]$Message = "Deploy web page"
)
$root = Split-Path $PSScriptRoot -Parent

function Invoke-Hf {
    & hf @args
    if ($LASTEXITCODE -ne 0) { throw "hf $($args[0]) failed (exit $LASTEXITCODE)" }
}

Invoke-Hf repos create $Space --repo-type space --space-sdk static --public --exist-ok
Invoke-Hf upload $Space "$root\web\index.html" index.html --repo-type space --commit-message $Message

$slug = $Space.Replace("/", "-").ToLower()
Write-Host "Deployed. Page: https://$slug.static.hf.space"
