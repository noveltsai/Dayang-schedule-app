$img = "2026_05_.jpg"
$model = "gemini-2.5-pro"
$attempt = 0
while ($true) {
    $attempt++
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] Attempt #$attempt ($model)..."
    python tools/ocr_upload.py $img --year 2026 --month 5 --no-review --model $model
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$ts] SUCCESS on attempt #$attempt"
        break
    }
    Write-Host "[$ts] Failed. Sleeping 30 min..."
    Start-Sleep -Seconds 1800
}
