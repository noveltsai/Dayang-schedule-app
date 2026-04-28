$img = "originals/2026-05.jpg"
$year = 2026
$month = 5

# 嘗試計畫：(model, attempts, cooldown_seconds)
$plan = @(
    @{ model = "gemini-3.1-pro-preview"; attempts = 2; cooldown = 300 },
    @{ model = "gemini-2.5-pro";         attempts = 1; cooldown = 0 }
)

$totalTry = 0
foreach ($step in $plan) {
    $model = $step.model
    $maxAttempts = $step.attempts
    $cooldown = $step.cooldown

    for ($i = 1; $i -le $maxAttempts; $i++) {
        $totalTry++
        $ts = Get-Date -Format "HH:mm:ss"
        Write-Host ""
        Write-Host "==========================================="
        Write-Host "[$ts] Try #$totalTry  $model  ($i/$maxAttempts)"
        Write-Host "==========================================="
        python tools/ocr_upload.py $img --year $year --month $month --no-review --model $model
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "[$ts] >>> SUCCESS with $model (try #$totalTry) <<<"
            exit 0
        }
        if ($i -lt $maxAttempts -and $cooldown -gt 0) {
            Write-Host "[$ts] Failed. Sleeping $cooldown sec..."
            Start-Sleep -Seconds $cooldown
        }
    }
    Write-Host "[$ts] $model exhausted, falling back to next..."
}

Write-Host ""
Write-Host "All models failed."
exit 1
