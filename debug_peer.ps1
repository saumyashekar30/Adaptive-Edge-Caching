$RESOURCE = "peer-test-$(Get-Random -Minimum 1000 -Maximum 9999)"
Write-Host "Resource: $RESOURCE"

# Step 1: Warm Node 1
Write-Host "`n--- Warming Node 1 ---"
$w = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
Write-Host "Node1 X-Cache: $($w.Headers['X-Cache'])"
Start-Sleep -Milliseconds 300

# Step 2: Verify Node 1 has it via /peer-cache/
Write-Host "`n--- Node 1 peer-cache probe ---"
try {
    $p = Invoke-WebRequest -Uri "http://127.0.0.1:8081/peer-cache/$RESOURCE" -UseBasicParsing -TimeoutSec 3
    Write-Host "Node1 /peer-cache/ -> X-Peer-Cache: $($p.Headers['X-Peer-Cache'])  Status: $($p.StatusCode)"
} catch {
    Write-Host "Node1 /peer-cache/ threw: $($_.Exception.Message)"
    $er = $_.Exception.Response
    if ($er) { Write-Host "  Status code: $($er.StatusCode.value__)" }
}

# Step 3: Invalidate Node 2's cache
Write-Host "`n--- Invalidating Node 2 cache ---"
try {
    $invalidateBody = [System.Text.Encoding]::UTF8.GetBytes("{`"key`": `"/data/$RESOURCE`"}")
    $inv = Invoke-RestMethod -Uri "http://127.0.0.1:8082/cache/invalidate" -Method Post -Body $invalidateBody -ContentType "application/json" -TimeoutSec 3
    Write-Host "Invalidate result: $($inv | ConvertTo-Json)"
} catch {
    Write-Host "Invalidate threw: $_"
}

# Step 4: Node 2 cold probe
Write-Host "`n--- Node 2 cold probe ---"
try {
    $cp = Invoke-WebRequest -Uri "http://127.0.0.1:8082/peer-cache/$RESOURCE" -UseBasicParsing -TimeoutSec 3
    Write-Host "Node2 /peer-cache/ -> X-Peer-Cache: $($cp.Headers['X-Peer-Cache'])  Status: $($cp.StatusCode)"
} catch {
    $er2 = $_.Exception.Response
    if ($er2) {
        Write-Host "Node2 /peer-cache/ -> HTTP $($er2.StatusCode.value__)  (expected 404 = cold)"
    } else {
        Write-Host "Node2 /peer-cache/ threw: $($_.Exception.Message)"
    }
}

# Step 5: THE ACTUAL PEER_HIT request
Write-Host "`n--- Requesting via Node 2 (expect PEER_HIT) ---"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8082/data/$RESOURCE" -UseBasicParsing -TimeoutSec 8
Write-Host "Status:         $($resp.StatusCode)"
Write-Host "X-Cache:        $($resp.Headers['X-Cache'])"
Write-Host "X-Peer-Node-Id: $($resp.Headers['X-Peer-Node-Id'])"
Write-Host "X-Edge-Node-Id: $($resp.Headers['X-Edge-Node-Id'])"

# Step 6: Check Node 1 peer-cache again (should still be HIT)
Write-Host "`n--- Node 1 peer-cache final verify ---"
try {
    $p2 = Invoke-WebRequest -Uri "http://127.0.0.1:8081/peer-cache/$RESOURCE" -UseBasicParsing -TimeoutSec 3
    Write-Host "Node1 /peer-cache/ -> X-Peer-Cache: $($p2.Headers['X-Peer-Cache'])  X-Peer-Node-Id: $($p2.Headers['X-Peer-Node-Id'])"
} catch {
    Write-Host "Node1 /peer-cache/ threw: $($_.Exception.Message)"
    $er3 = $_.Exception.Response
    if ($er3) { Write-Host "  Status: $($er3.StatusCode.value__)" }
}
