$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8081/data/ps-header-type-test' -UseBasicParsing -TimeoutSec 5
Write-Host "Headers type: $($r.Headers.GetType().FullName)"
Write-Host "---"
$r.Headers.GetEnumerator() | ForEach-Object {
    Write-Host "$($_.Key) = $($_.Value)"
}
