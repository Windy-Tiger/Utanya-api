$key = "2026Utanya232811!"
$base = "https://utanya-api-production.up.railway.app"

$files = Get-ChildItem "C:\Users\bruno\utanya-api\bolletins\boletim_*.json"
Write-Host "Re-ingesting $($files.Count) files..."
$i = 0
foreach ($f in $files) {
    $i++
    Write-Host "[$i/$($files.Count)] $($f.Name)"
    curl.exe -s -m 60 -X POST "$base/rag/ingest/bulletin-json" -H "X-API-Key: $key" -F "file=@$($f.FullName)"
    Write-Host ""
    Start-Sleep -Seconds 7
}
Write-Host "DONE"