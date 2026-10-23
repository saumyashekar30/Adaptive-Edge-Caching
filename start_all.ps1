# ============================================================
#  START ALL SERVICES — run once before demo.ps1
#  From the Adaptive-Edge-Caching project root:
#    powershell -ExecutionPolicy Bypass -File start_all.ps1
# ============================================================

$root = $PSScriptRoot

function LaunchService($label, $cmd) {
    Write-Host "[START] $label" -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; $cmd" -PassThru | Out-Null
    Start-Sleep -Milliseconds 800
}

Write-Host ""
Write-Host "Starting Adaptive Edge Caching services..." -ForegroundColor White
Write-Host ""

LaunchService "Origin Server          (port 8000)" `
    "python -m src.origin.server --port 8000 --latency-ms 50"

LaunchService "Edge Node 1            (port 8081)" `
    "python -m src.edge.node --config config/nodes/node1.yaml"

LaunchService "Edge Node 2            (port 8082)" `
    "python -m src.edge.node --config config/nodes/node2.yaml"

LaunchService "Edge Node 3            (port 8083)" `
    "python -m src.edge.node --config config/nodes/node3.yaml"

LaunchService "Traffic Controller     (port 9000)" `
    "python -m src.routing.controller --port 9000 --config config/routing.yaml"

Write-Host ""
Write-Host "Waiting 3 seconds for all processes to bind..." -ForegroundColor DarkGray
Start-Sleep -Seconds 3
Write-Host ""

# Quick health check
$services = @(
    @{name="Origin      :8000"; url="http://127.0.0.1:8000/health"},
    @{name="Node 1      :8081"; url="http://127.0.0.1:8081/health"},
    @{name="Node 2      :8082"; url="http://127.0.0.1:8082/health"},
    @{name="Node 3      :8083"; url="http://127.0.0.1:8083/health"},
    @{name="Controller  :9000"; url="http://127.0.0.1:9000/health"}
)

foreach ($s in $services) {
    try {
        $r = Invoke-RestMethod -Uri $s.url -TimeoutSec 3
        Write-Host "  [OK] $($s.name)  status=$($r.status)" -ForegroundColor Green
    } catch {
        Write-Host "  [!!] $($s.name)  not responding yet" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "All services launched. Run demo.ps1 to begin the presentation demo." -ForegroundColor Cyan
Write-Host ""
