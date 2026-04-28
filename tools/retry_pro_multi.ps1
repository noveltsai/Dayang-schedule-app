param(
    [Parameter(Mandatory=$true)]
    [int[]]$Months,
    [int]$Year = 2026
)

# Force Python UTF-8 console (cp950 chokes on emoji)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$plan = @(
    @{ model = "gemini-3.1-pro-preview"; attempts = 2; cooldown = 300 },
    @{ model = "gemini-2.5-pro";         attempts = 1; cooldown = 0 }
)

$results = @{}

foreach ($m in $Months) {
    $mm = "{0:D2}" -f $m
    $img = "originals/$Year-$mm.jpg"
    if (-not (Test-Path $img)) {
        Write-Host "Missing $img, skip"
        $results[$m] = "missing"
        continue
    }

    Write-Host ""
    Write-Host "###########################################"
    Write-Host "###  $Year-$mm  ###"
    Write-Host "###########################################"

    $totalTry = 0
    $ok = $false
    foreach ($step in $plan) {
        if ($ok) { break }
        $model = $step.model
        $maxAttempts = $step.attempts
        $cooldown = $step.cooldown
        for ($i = 1; $i -le $maxAttempts; $i++) {
            $totalTry++
            $ts = Get-Date -Format "HH:mm:ss"
            Write-Host ""
            Write-Host "[$ts] $Year-$mm try #$totalTry  $model  ($i/$maxAttempts)"
            python tools/ocr_upload.py $img --year $Year --month $m --no-review --model $model
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[$ts] SUCCESS $Year-$mm  $model"
                $results[$m] = "ok ($model try#$totalTry)"
                $ok = $true
                break
            }
            if ($i -lt $maxAttempts -and $cooldown -gt 0) {
                Write-Host "[$ts] Failed. Sleeping $cooldown sec..."
                Start-Sleep -Seconds $cooldown
            }
        }
        if (-not $ok) {
            Write-Host "[$ts] $model exhausted, falling back..."
        }
    }
    if (-not $ok) {
        $results[$m] = "FAIL"
    }
}

Write-Host ""
Write-Host "===== Summary ====="
foreach ($k in ($results.Keys | Sort-Object)) {
    $mm = "{0:D2}" -f $k
    Write-Host "  $Year-$mm  -->  $($results[$k])"
}
