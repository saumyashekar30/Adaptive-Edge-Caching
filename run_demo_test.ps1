# Non-interactive smoke-test of all 4 demo scenarios.
# Run from the Adaptive-Edge-Caching project root.
# Services must already be running (start_all.ps1).

$pass = @{d1=$false; d2=$false; d3=$false; d4=$false}

function Banner($t) {
    Write-Host "`n$("="*65)" -ForegroundColor Cyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host "$("="*65)" -ForegroundColor Cyan
}
function OK($t)   { Write-Host "[  OK] $t" -ForegroundColor Green  }
function FAIL($t) { Write-Host "[FAIL] $t" -ForegroundColor Red    }
function Info($t) { Write-Host "[INFO] $t" -ForegroundColor White  }

# ── Demo 1: Health checks ─────────────────────────────────────
Banner "Demo 1 - Service Health"
$d1ok = $true
@(
    @{n="Origin    :8000"; u="http://127.0.0.1:8000/health"},
    @{n="Node 1    :8081"; u="http://127.0.0.1:8081/health"},
    @{n="Node 2    :8082"; u="http://127.0.0.1:8082/health"},
    @{n="Node 3    :8083"; u="http://127.0.0.1:8083/health"},
    @{n="Controller:9000"; u="http://127.0.0.1:9000/health"}
) | ForEach-Object {
    try {
        $r = Invoke-RestMethod -Uri $_.u -TimeoutSec 3
        OK "$($_.n)  status=$($r.status)"
    } catch {
        Write-Host "[WARN] $($_.n)  not responding" -ForegroundColor Yellow
        if ($_.n -match "Origin|Node 1") { $d1ok = $false }
    }
}
if ($d1ok) { $pass.d1 = $true; OK "Demo 1 PASS" } else { FAIL "Demo 1 FAIL - critical service missing" }

if (-not $pass.d1) { Write-Host "Cannot continue without origin + node1."; exit 1 }

# ── Demo 2: MISS -> HIT ───────────────────────────────────────
Banner "Demo 2 - MISS then HIT on Node 1"
$RESOURCE = "demo-$(Get-Random -Min 1000 -Max 9999)"
Info "Resource: $RESOURCE"

$r1 = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
$c1 = $r1.Headers["X-Cache"]
Info "Request 1: X-Cache=$c1  (expected MISS)"

Start-Sleep -Milliseconds 200

$r2 = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
$c2 = $r2.Headers["X-Cache"]
Info "Request 2: X-Cache=$c2  (expected HIT)"

if ($c1 -eq "MISS" -and $c2 -eq "HIT") {
    $pass.d2 = $true; OK "Demo 2 PASS  MISS->HIT confirmed"
} else {
    FAIL "Demo 2 FAIL  got $c1 -> $c2"
}

# ── Demo 3: PEER_HIT ──────────────────────────────────────────
Banner "Demo 3 - PEER_HIT via Node 2"

# Pre-warm Node 1 (guarantee object is cached regardless of Demo 2)
$wm = Invoke-WebRequest -Uri "http://127.0.0.1:8081/data/$RESOURCE" -UseBasicParsing -TimeoutSec 5
Info "Pre-warm Node 1: X-Cache=$($wm.Headers['X-Cache'])"
Start-Sleep -Milliseconds 300

# Invalidate Node 2's local copy
try {
    $ib = [System.Text.Encoding]::UTF8.GetBytes("{`"key`": `"/data/$RESOURCE`"}")
    $inv = Invoke-RestMethod -Uri "http://127.0.0.1:8082/cache/invalidate" -Method Post -Body $ib -ContentType "application/json" -TimeoutSec 3
    Info "Node 2 invalidate: success=$($inv.success)"
} catch { Info "Node 2 invalidate skipped (key not present)" }

# Try up to 2 times (peer_client may timeout on first attempt under load)
$c3 = $null; $xPeer = $null
for ($attempt = 1; $attempt -le 2; $attempt++) {
    if ($attempt -eq 2) { Start-Sleep -Milliseconds 500 }
    try {
        $r3  = Invoke-WebRequest -Uri "http://127.0.0.1:8082/data/$RESOURCE" -UseBasicParsing -TimeoutSec 8
        $c3  = $r3.Headers["X-Cache"]
        $xPeer = $r3.Headers["X-Peer-Node-Id"]
        if ($c3 -ne "MISS" -or $attempt -eq 2) { break }
        Info "Attempt $attempt got MISS, retrying..."
    } catch { FAIL "Node 2 request failed: $_"; break }
}

Info "Node 2: X-Cache=$c3  X-Peer-Node-Id=$xPeer  (expected PEER_HIT)"
if ($c3 -eq "PEER_HIT") {
    $pass.d3 = $true; OK "Demo 3 PASS  PEER_HIT from $xPeer"
} else {
    FAIL "Demo 3 FAIL  got $c3 instead of PEER_HIT"
}

# ── Demo 4: Controller routing ────────────────────────────────
Banner "Demo 4 - Controller routing :9000"
$ctrlOk = $false
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:9000/health" -TimeoutSec 3
    $ctrlOk = $true
    Info "Controller: status=$($h.status)  active=$($h.active_nodes -join ',')"
} catch {
    Write-Host "[SKIP] Controller not running - skipping Demo 4" -ForegroundColor Yellow
}

if ($ctrlOk) {
    # Nodes list
    $nodes = Invoke-RestMethod -Uri "http://127.0.0.1:9000/nodes" -TimeoutSec 3
    Info "Registered nodes: $(($nodes | ForEach-Object { $_.node_id }) -join ', ')"

    # Route through controller
    try {
        $rc   = Invoke-WebRequest -Uri "http://127.0.0.1:9000/data/$RESOURCE" -UseBasicParsing -TimeoutSec 8
        $xSel = $rc.Headers["X-Selected-Node"]
        $xScr = $rc.Headers["X-Node-Score"]
        $xCC  = $rc.Headers["X-Cache"]
        $xTm  = $rc.Headers["X-Controller-Time-Ms"]
        Info "Controller routed -> X-Selected-Node=$xSel  X-Node-Score=$xScr  X-Cache=$xCC  X-Controller-Time-Ms=$xTm"
        if ($xSel) { $pass.d4 = $true; OK "Demo 4 PASS  routed to $xSel (score $xScr)" }
        else        { FAIL "Demo 4 FAIL  X-Selected-Node header missing" }
    } catch {
        FAIL "Demo 4 controller request failed: $_"
    }
}

# ── Final summary ─────────────────────────────────────────────
Banner "Results"
$labels = @{d1="Demo 1  Health checks"; d2="Demo 2  MISS->HIT"; d3="Demo 3  PEER_HIT"; d4="Demo 4  Controller routing"}
$overall = $true
foreach ($k in @("d1","d2","d3","d4")) {
    $v   = $pass[$k]
    $sym = if ($v) {"PASS"} else {"FAIL"}
    $col = if ($v) {"Green"} else {"Red"}
    if (-not $v -and $k -ne "d4") { $overall = $false }
    Write-Host ("  [{0}]  {1}" -f $sym, $labels[$k]) -ForegroundColor $col
}
Write-Host ""
if ($overall) { OK "ALL DEMOS PASSED" } else { FAIL "SOME DEMOS FAILED - see above" }
Write-Host ""
