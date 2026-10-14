#!/bin/bash
# apply_tc_netem.sh - Shared Seam between Saumya (Simulation/Routing) and Riddhi (Docker/Deployment)
# Applies NS-3 simulation network impairment profiles (delay, packet loss, bandwidth limit) to Docker edge nodes using tc netem.

set -e

CONTAINER_NAME=${1:-"edge-node-2"}
INTERFACE=${2:-"eth0"}
SCENARIO=${3:-"normal"}

echo "======================================================================"
echo "Applying NS-3 Simulation Parameters to Docker Container: ${CONTAINER_NAME}"
echo "Scenario: ${SCENARIO} on interface: ${INTERFACE}"
echo "======================================================================"

# Reset existing traffic control rules
docker exec --privileged "${CONTAINER_NAME}" tc qdisc del dev "${INTERFACE}" root 2>/dev/null || true

case "${SCENARIO}" in
    "normal")
        echo "[tc netem] Nominal link: 5ms delay, 0% loss, 100Mbps rate"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem delay 5ms rate 100mbit
        ;;
    "high_latency")
        echo "[tc netem] High latency link: 180ms delay, 15ms jitter, 100Mbps rate"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem delay 180ms 15ms distribution normal rate 100mbit
        ;;
    "packet_loss")
        echo "[tc netem] Lossy link: 15ms delay, 8.5% packet loss"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem delay 15ms loss 8.5% rate 100mbit
        ;;
    "reduced_bandwidth")
        echo "[tc netem] Throttled link: 15ms delay, 1Mbps bandwidth limit"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem delay 15ms rate 1mbit
        ;;
    "congestion")
        echo "[tc netem] Congested buffer: 10ms delay, shallow queue (20 pkts), 3.5% loss, 10Mbps rate"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem delay 10ms loss 3.5% limit 20 rate 10mbit
        ;;
    "link_failure")
        echo "[tc netem] Link failure: 100% packet loss (simulating complete link disconnect)"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc add dev "${INTERFACE}" root netem loss 100%
        ;;
    "restore")
        echo "[tc netem] Removing all traffic control rules on ${CONTAINER_NAME}"
        docker exec --privileged "${CONTAINER_NAME}" tc qdisc del dev "${INTERFACE}" root 2>/dev/null || true
        ;;
    *)
        echo "Unknown scenario: ${SCENARIO}. Supported: normal, high_latency, packet_loss, reduced_bandwidth, congestion, link_failure, restore"
        exit 1
        ;;
esac

echo "[SUCCESS] Applied network profile '${SCENARIO}' to ${CONTAINER_NAME}."
docker exec --privileged "${CONTAINER_NAME}" tc qdisc show dev "${INTERFACE}"
