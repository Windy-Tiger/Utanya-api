$key = "2026Utanya232811!"
$base = "https://utanya-api-production.up.railway.app"

$docs = curl.exe -s "$base/rag/documents" -H "X-API-Key: $key" | ConvertFrom-Json
$bulletins = $docs | Where-Object { $_.doc_type -eq "bulletin" }
Write-Host "Deleting $($bulletins.Count) bulletins..."
foreach ($d in $bulletins) { curl.exe -s -X DELETE "$base/rag/documents/$($d.id)" -H "X-API-Key: $key" | Out-Null }
Write-Host "Deleted. Re-ingesting..."

$files = Get-ChildItem "C:\Users\bruno\utanya-api\bolletins\boletim_*.json"
$i = 0
foreach ($f in $files) {
    $i++
    Write-Host "[$i/$($files.Count)] $($f.Name)"
    curl.exe -s -m 60 -X POST "$base/rag/ingest/bulletin-json" -H "X-API-Key: $key" -F "file=@$($f.FullName)" | Out-Null
    Start-Sleep -Seconds 7
}
Write-Host "DONE"