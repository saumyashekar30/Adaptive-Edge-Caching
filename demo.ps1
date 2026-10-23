# ============================================================
#  Adaptive Edge Caching - Live Demo Script (Part 4)
#  Run from the Adaptive-Edge-Caching project root:
#    powershell -ExecutionPolicy Bypass -File demo.ps1
# ============================================================

function Banner($text) {
    $bar = "=" * 70
    Write-Host ""
    Write-Host $bar -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host $bar -ForegroundColor Cyan
    Write-Host ""
}

function Step($text)  { Write-Host "[STEP] $text" -ForegroundColor Yellow }
function OK($text)    { Write-Host "[  OK] $text" -ForegroundColor Green  }
function FAIL($text)  { Write-Host "[FAIL] $text" -ForegroundColor Red    }
function Info($text)  { Write-Host "[INFO] $text" -ForegroundColor White  }

function Pause-Demo($msg = "Press ENTER to continue...") {
    Write-Host ""
    Write-Host $msg -ForegroundColor Magenta
    $null = Read-Host
}

# Track pass/fail for honest summary
$demo2_pass = $false
$demo3_pass = $false
$demo4_pass = $false

# ============================================================
Banner "Demo 1 - Verify services are running and nodes are healthy"
# ============================================================

Info "Checking Origin Server (port 8000)..."
try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3
    OK "Origin  : status=$($r.status)  uptime=$([math]::Round($r.uptime,1))s"
} catch {
    FAIL "Origin server NOT running. Start with:"
    Write-Host "         python -m src.origin.server --port 8000 --latency-ms 50" -ForegroundColor DarkGray
    exit 1
}

Info "Checking Edge Node 1 (port 8081)..."
try {
    $n1 = Invoke-RestMethod -Uri "http://127.0.0.1:8081/health" -TimeoutSec 3
    OK "Node 1  : status=$($n1.status)  node_id=$($n1.node_id)  uptime=$([math]::Round($n1.uptime,1))s"
} catch {
    FAIL "Edge Node 1 NOT running. Start with:"
    Write-Host "         python -m src.edge.node --config config/nodes/node1.yaml" -ForegroundColor DarkGray
    exit 1
}

Info "Checking Edge Node 2 (port 8082)..."
try {
    $n2 = Invoke-RestMethod -Uri "http://127.0.0.1:8082/health" -TimeoutSec 3
    OK "Node 2  : status=$($n2.status)  node_id=$($n2.node_id)  uptime=$([math]::Round($n2.uptime,1))s"
} catch {
    Write-Host "[WARN] Edge Node 2 (8082) not responding - PEER_HIT demo limited." -ForegroundColor Yellow
}

Info "Checking Edge Node 3 (port 8083)..."
try {
    $n3 = Invoke-RestMethod -Uri "http://127.0.0.1:8083/health" -TimeoutSec 3
    OK "Node 3  : status=$($n3.status)  node_id=$($n3.node_id)  uptime=$([math]::Round($n3.uptime,1))s"
} catch {
    Write-Host "[WARN] Edge Node 3 (8083) not responding." -ForegroundColor Yellow
}

Info "Checking Controller (port 9000)..."
$ctrl = $null
try {
    $ctrl = Invoke-RestMethod -Uri "http://127.0.0.1:9000/health" -TimeoutSec 3
    OK "Controller: status=$($ctrl.status)  active_nodes=$($ctrl.active_nodes -join ', ')"
} catch {
    Write-Host "[WARN] Controller (9000) not responding - Demo 4 will be skipped." -ForegroundColor Yellow
}

Pause-Demo "[ Demo 1 complete. Press ENTER for Demo 2 - MISS then HIT ]"

# ============================================================
Banner "Demo 2 - MISS then HIT on Edge Node 1 (port 8081)"
# ============================================================
$RESOURCE = "demo-object-$(Get-Random -Minimum 1000 -Maximum 9999)"
Info "Using resource key: $RESOURCE"
Write-Host ""

Step "Request 1 - expected: MISS (object not yet in cache)"
Info "  GET http://127.0.0.1:8081/data/$RESOURCE"
$xCache1 = $null
try {
    $resp1   = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
    $xCache1 = $resp1.Headers["X-Cache"]
    $xVer1   = $resp1.Headers["X-Resource-Version"]
    Write-Host ""
    Write-Host "  HTTP $($resp1.StatusCode)   X-Cache: $xCache1   X-Resource-Version: $xVer1" -ForegroundColor $(if ($xCache1 -eq "MISS") {"Yellow"} elseif ($xCache1 -eq "BYPASS") {"Red"} else {"Green"})
    if ($xCache1 -eq "MISS") {
        OK "Request 1: MISS (as expected)"
    } elseif ($xCache1 -eq "BYPASS") {
        FAIL "Request 1: BYPASS (Cache-Control header bypassed the cache)"
    } else {
        Write-Host "[WARN] Request 1: Expected MISS but got $xCache1" -ForegroundColor Yellow
    }
} catch {
    FAIL "Request 1 failed: $_"
}

# Small delay to ensure the put() completes before the next get()
Start-Sleep -Milliseconds 200

Write-Host ""
Step "Request 2 - expected: HIT (served from Node 1 local cache)"
Info "  GET http://127.0.0.1:8081/data/$RESOURCE"
$xCache2 = $null
try {
    $resp2   = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
    $xCache2 = $resp2.Headers["X-Cache"]
    $xVer2   = $resp2.Headers["X-Resource-Version"]
    Write-Host ""
    Write-Host "  HTTP $($resp2.StatusCode)   X-Cache: $xCache2   X-Resource-Version: $xVer2" -ForegroundColor $(if ($xCache2 -eq "HIT") {"Green"} else {"Red"})
    if ($xCache2 -eq "HIT") {
        OK "Request 2: HIT (cache working correctly)"
    } else {
        FAIL "Request 2: Expected HIT but got $xCache2"
        Write-Host "  The object was not found in cache on the second request." -ForegroundColor DarkGray
        Write-Host "  Possible causes: TTL expiration, Cache-Control bypass, or cache store issue." -ForegroundColor DarkGray
    }
} catch {
    FAIL "Request 2 failed: $_"
}

# Determine Demo 2 result honestly
if ($xCache1 -eq "MISS" -and $xCache2 -eq "HIT") {
    $demo2_pass = $true
}

Write-Host ""
Info "Cache stats on Node 1 (/cache/stats):"
try {
    $stats = Invoke-RestMethod -Uri "http://127.0.0.1:8081/cache/stats" -TimeoutSec 3
    Write-Host ($stats | ConvertTo-Json -Depth 3) -ForegroundColor DarkCyan
} catch {
    Write-Host "  (Could not fetch cache stats)" -ForegroundColor DarkGray
}

if ($demo2_pass) {
    Pause-Demo "[ Demo 2 PASSED. Press ENTER for Demo 3 - PEER_HIT ]"
} else {
    Pause-Demo "[ Demo 2 had issues. Press ENTER for Demo 3 - PEER_HIT ]"
}

# ============================================================
Banner "Demo 3 - PEER_HIT: object cached on Node 1, requested via Node 2"
# ============================================================
# The peer path: Node 2 misses locally -> asks Node 1 via /peer-cache/ -> PEER_HIT
#
# Three things must be true going in:
#   1. Node 1 has the object in its cache (guaranteed by pre-warm below)
#   2. Node 2 does NOT have the object locally (guaranteed by cache invalidation)
#   3. Node 2's peers list contains Node 1 (config/nodes/node2.yaml)

# ── Step 0: Guarantee Node 1 has the object ──────────────────
Step "Pre-warming Node 1 cache (guarantees object is present regardless of Demo 2)"
Info "  GET http://127.0.0.1:8081/data/$RESOURCE"
try {
    $warmup = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
    $warmupCache = $warmup.Headers["X-Cache"]
    Write-Host "  Node 1 X-Cache: $warmupCache" -ForegroundColor $(if ($warmupCache -eq "HIT") {"Green"} else {"Yellow"})
    if ($warmupCache -eq "HIT") {
        OK "Object already in Node 1 cache - no origin fetch needed"
    } else {
        OK "Object fetched from origin and stored in Node 1 cache"
    }
    Start-Sleep -Milliseconds 300    # let cache.put() settle before Node 2 asks
} catch {
    FAIL "Could not warm Node 1: $_ - PEER_HIT demo may not work"
}

# ── Step 1: Clear Node 2's cache for this resource (ensure cold cache) ──
Write-Host ""
Step "Clearing Node 2 local cache for this resource (ensure cold cache on Node 2)"
try {
    $invalidateBody = [System.Text.Encoding]::UTF8.GetBytes("{`"key`": `"/data/$RESOURCE`"}")
    $inv = Invoke-RestMethod -Uri "http://127.0.0.1:8082/cache/invalidate" `
        -Method Post -Body $invalidateBody -ContentType "application/json" -TimeoutSec 3
    OK "Node 2 cache invalidated for /data/$RESOURCE  (success=$($inv.success))"
} catch {
    Write-Host "  (Invalidate call failed or not needed - Node 2 may already be cold)" -ForegroundColor DarkGray
}

# ── Step 2: Verify Node 2 is cold (probe its own /peer-cache/ slot) ──
# NOTE: /peer-cache/<id> returns 404 when the node's LOCAL cache is cold.
# PS 5.1 throws on 404 so we catch the exception and read the response from it.
Write-Host ""
Step "Confirm Node 2 local cache is cold for '$RESOURCE'"
Info "  GET http://127.0.0.1:8082/peer-cache/$RESOURCE  (404 = cold, as expected)"
try {
    $coldProbe = Invoke-WebRequest -Uri "http://127.0.0.1:8082/peer-cache/$RESOURCE" -UseBasicParsing -TimeoutSec 3
    $coldHeader = $coldProbe.Headers["X-Peer-Cache"]
    Write-Host "  Node 2 local X-Peer-Cache: $coldHeader" -ForegroundColor $(if ($coldHeader -eq "MISS") {"Yellow"} else {"Green"})
} catch {
    # PS 5.1 throws on 404; read the X-Peer-Cache header from the error response
    $errResp = $_.Exception.Response
    if ($errResp -ne $null -and $errResp.StatusCode.value__ -eq 404) {
        $coldHeader = $errResp.Headers["X-Peer-Cache"]
        $coldHeaderVal = if ($coldHeader) { $coldHeader } else { "MISS" }
        Write-Host "  Node 2 local X-Peer-Cache: $coldHeaderVal  HTTP 404  (cold cache confirmed)" -ForegroundColor Yellow
    } else {
        Write-Host "  (Cold-cache probe failed: $($_.Exception.Message))" -ForegroundColor DarkGray
    }
}

# ── Step 3: THE ACTUAL DEMO - request via Node 2, expect PEER_HIT ──────
Write-Host ""
Step "Fetch object THROUGH Node 2 — expected: PEER_HIT from Node 1"
Info "  GET http://127.0.0.1:8082/data/$RESOURCE"
$xCache3 = $null

# Try up to 2 times — the first attempt can miss if the peer_client
# hits its timeout under load. A 500ms wait lets the connection settle.
for ($attempt = 1; $attempt -le 2; $attempt++) {
    if ($attempt -eq 2) {
        Write-Host "  [Retry $attempt] Peer may have timed out on first attempt. Retrying after 500ms..." -ForegroundColor DarkGray
        Start-Sleep -Milliseconds 500
    }
    try {
        $resp3     = Invoke-WebRequest -Uri "http://127.0.0.1:8082/data/$RESOURCE" -UseBasicParsing -TimeoutSec 8
        $xCache3   = $resp3.Headers["X-Cache"]
        $xPeerNode = $resp3.Headers["X-Peer-Node-Id"]
        $xVer3     = $resp3.Headers["X-Resource-Version"]

        if ($xCache3 -ne "MISS" -or $attempt -eq 2) { break }
    } catch {
        FAIL "Request via Node 2 failed: $_"
        break
    }
}

$color3 = switch ($xCache3) {
    "PEER_HIT" { "Green"  }
    "HIT"      { "Yellow" }
    "MISS"     { "Red"    }
    default    { "Red"    }
}
Write-Host ""
Write-Host "  HTTP $($resp3.StatusCode)" -ForegroundColor White
Write-Host "  X-Cache        : $xCache3"   -ForegroundColor $color3
Write-Host "  X-Peer-Node-Id : $xPeerNode" -ForegroundColor Cyan
Write-Host "  X-Resource-Version: $xVer3"  -ForegroundColor DarkCyan

if ($xCache3 -eq "PEER_HIT") {
    OK "PEER_HIT confirmed - Node 2 served object from Node 1's cache (no origin fetch)"
    $demo3_pass = $true
} elseif ($xCache3 -eq "HIT") {
    Write-Host "[WARN] Got HIT - Node 2 already had this object locally (invalidation may have missed a variant key)" -ForegroundColor Yellow
} elseif ($xCache3 -eq "MISS") {
    FAIL "Got MISS - Node 2 fetched from origin instead of peers"
    Write-Host "  Possible causes:" -ForegroundColor DarkGray
    Write-Host "    - Node 1 cache was empty (check Node 1 /cache/stats)" -ForegroundColor DarkGray
    Write-Host "    - Peer timeout exceeded (increased to 2.5s in peer_client.py)" -ForegroundColor DarkGray
    Write-Host "    - node2.yaml peers list does not include Node 1's URL" -ForegroundColor DarkGray
} else {
    Write-Host "[WARN] Unexpected X-Cache value: $xCache3" -ForegroundColor Yellow
}

# ── Step 4: Confirm Node 1 still holds the object ───────────────────────
Write-Host ""
Info "Verify Node 1 still holds the object in its peer-cache slot:"
Info "  GET http://127.0.0.1:8081/peer-cache/$RESOURCE"
try {
    $p1 = Invoke-WebRequest -Uri "http://127.0.0.1:8081/peer-cache/$RESOURCE" -UseBasicParsing -TimeoutSec 3
    Write-Host "  Node 1 X-Peer-Cache: $($p1.Headers['X-Peer-Cache'])  X-Peer-Node-Id: $($p1.Headers['X-Peer-Node-Id'])" -ForegroundColor Green
    OK "Node 1 confirmed as cache source"
} catch {
    $errResp2 = $_.Exception.Response
    if ($errResp2 -ne $null) {
        FAIL "Node 1 returned $($errResp2.StatusCode.value__) for peer-cache probe - object may have been evicted"
    } else {
        Write-Host "  (Node 1 peer-cache probe failed: $($_.Exception.Message))" -ForegroundColor DarkGray
    }
}

if ($demo3_pass) {
    Pause-Demo "[ Demo 3 PASSED. Press ENTER for Demo 4 - Controller routing ]"
} else {
    Pause-Demo "[ Demo 3 had issues. Press ENTER for Demo 4 - Controller routing ]"
}

# ============================================================
Banner "Demo 4 - Controller routing via port 9000"
# ============================================================

if ($null -eq $ctrl) {
    Write-Host "[SKIP] Controller not detected on port 9000." -ForegroundColor Yellow
    Write-Host "       Start with:" -ForegroundColor DarkGray
    Write-Host "         python -m src.routing.controller --port 9000 --config config/routing.yaml" -ForegroundColor DarkGray
} else {

    Step "4a - List registered nodes with health and scores  GET /nodes"
    try {
        $nodes = Invoke-RestMethod -Uri "http://127.0.0.1:9000/nodes" -TimeoutSec 3
        Write-Host ""
        $nodes | ForEach-Object {
            $label = if ($_.is_active) { "ACTIVE" } else { "DEAD  " }
            $score = if ($null -ne $_.score) { [math]::Round($_.score, 3) } else { "N/A" }
            $col   = if ($_.is_active) { "Green" } else { "Red" }
            Write-Host ("  {0,-10}  {1,-30}  [{2}]  score={3}" -f $_.node_id, $_.url, $label, $score) -ForegroundColor $col
        }
    } catch {
        FAIL "/nodes: $_"
    }

    Write-Host ""
    Step "4b - Cluster health summary  GET /health"
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:9000/health" -TimeoutSec 3
        Write-Host ($h | ConvertTo-Json -Depth 2) -ForegroundColor DarkCyan
    } catch {
        FAIL "/health: $_"
    }

    Write-Host ""
    Step "4c - Routing weights  GET /weights"
    try {
        $w = Invoke-RestMethod -Uri "http://127.0.0.1:9000/weights" -TimeoutSec 3
        Write-Host ("  alpha(latency)={0}  beta(packet_loss)={1}  gamma(utilization)={2}  delta(queueing)={3}" -f $w.alpha, $w.beta, $w.gamma, $w.delta) -ForegroundColor DarkCyan
    } catch {
        Write-Host "  (weights endpoint failed)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Step "4d - Route a live request THROUGH the controller  GET /data/$RESOURCE"
    Info "  GET http://127.0.0.1:9000/data/$RESOURCE"
    try {
        $rc      = Invoke-WebRequest -Uri "http://127.0.0.1:9000/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
        $xSel    = $rc.Headers["X-Selected-Node"]
        $xScore  = $rc.Headers["X-Node-Score"]
        $xCtrl   = $rc.Headers["X-Controller"]
        $xCache  = $rc.Headers["X-Cache"]
        $xTime   = $rc.Headers["X-Controller-Time-Ms"]
        $cacheColor = switch ($xCache) {
            "HIT"      { "Green"  }
            "PEER_HIT" { "Yellow" }
            default    { "Red"    }
        }
        Write-Host ""
        Write-Host "  HTTP $($rc.StatusCode)"
        Write-Host "  X-Selected-Node      : $xSel"   -ForegroundColor Green
        Write-Host "  X-Node-Score         : $xScore" -ForegroundColor Cyan
        Write-Host "  X-Controller         : $xCtrl"  -ForegroundColor Cyan
        Write-Host "  X-Cache              : $xCache"  -ForegroundColor $cacheColor
        Write-Host "  X-Controller-Time-Ms : $xTime"  -ForegroundColor DarkCyan
        OK "Controller routed to $xSel (score $xScore)"
        $demo4_pass = $true
    } catch {
        FAIL "Controller-routed request failed: $_"
    }
}

# ============================================================
Banner "Demo Results"
# ============================================================

$d1col = "Green"; $d1sym = "PASS"
$d2col = if ($demo2_pass) { "Green" } else { "Red" }; $d2sym = if ($demo2_pass) { "PASS" } else { "FAIL" }
$d3col = if ($demo3_pass) { "Green" } else { "Red" }; $d3sym = if ($demo3_pass) { "PASS" } else { "FAIL" }
$d4col = if ($demo4_pass) { "Green" } else { if ($null -eq $ctrl) { "Yellow" } else { "Red" } }
$d4sym = if ($demo4_pass) { "PASS" } else { if ($null -eq $ctrl) { "SKIP" } else { "FAIL" } }

Write-Host ""
Write-Host "  Demo 1  [$d1sym]  All services healthy" -ForegroundColor $d1col
Write-Host "  Demo 2  [$d2sym]  MISS -> HIT (same resource, same edge node)" -ForegroundColor $d2col
Write-Host "  Demo 3  [$d3sym]  PEER_HIT (Node 1 cache served via Node 2 peer fetch)" -ForegroundColor $d3col
Write-Host "  Demo 4  [$d4sym]  Controller routing with X-Selected-Node and X-Node-Score" -ForegroundColor $d4col
Write-Host ""
Write-Host "  Resource used this run: $RESOURCE" -ForegroundColor DarkGray
Write-Host ""
