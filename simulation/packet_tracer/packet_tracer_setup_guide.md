# Cisco Packet Tracer 8.x Setup & Verification Guide

This guide provides instructions to construct and validate the **Adaptive Edge Caching** network architecture in Cisco Packet Tracer.

---

## 1. Required Inventory

| Device Model | Name in Topology | Quantity | Notes |
|---|---|---|---|
| **Cisco 4331 Router** | `R1-Edge-Router`, `R2-Core-Origin` | 2 | ISR4331/K9 with 3 GigabitEthernet ports |
| **Catalyst 2960 Switch** | `SW-Client`, `SW-Edge`, `SW-Origin` | 3 | 24 FastEthernet + 2 Gigabit Uplinks |
| **Server-PT** | `Edge-Node-1`, `Edge-Node-2`, `Edge-Node-3`, `Origin-Server` | 4 | Enable HTTP services and configure static IPs |
| **PC-PT** | `PC-1`, `PC-2` | 2 | Workstations simulating clients |
| **Copper Straight-Through** | FastEthernet / Gigabit cables | 11 | Standard RJ-45 patch cables |

---

## 2. Step-by-Step Construction

### Step 1: Place and Connect Devices
1. Place **R1-Edge-Router** and **R2-Core-Origin** in the center.
2. Connect `R1 G0/0/1` to `R2 G0/0/0` with a Copper Straight-Through cable (or crossover).
3. Connect `R1 G0/0/0` to `SW-Client G0/1`.
4. Connect `R1 G0/0/2` to `SW-Edge G0/1` (this serves as the 802.1Q trunk link).
5. Connect `R2 G0/0/1` to `SW-Origin G0/1`.
6. Connect `PC-1` and `PC-2` to `SW-Client Fa0/1` and `Fa0/2`.
7. Connect `Edge-Node-1`, `Edge-Node-2`, `Edge-Node-3` to `SW-Edge Fa0/1`, `Fa0/2`, and `Fa0/3`.
8. Connect `Origin-Server` to `SW-Origin Fa0/1`.

### Step 2: Apply Device Configurations
- Open the CLI tab of **R1-Edge-Router** and paste the R1 block from `cisco_device_configs.txt`.
- Open the CLI tab of **R2-Core-Origin** and paste the R2 block from `cisco_device_configs.txt`.
- Open the CLI tab of **SW-Edge** and paste the switch trunk/VLAN block from `cisco_device_configs.txt`.

### Step 3: Configure Server & PC Endpoints
1. **Edge-Node-1:**
   - IP: `10.0.1.10`, Mask: `255.255.255.0`, Gateway: `10.0.1.1`
   - Services -> HTTP: On (Edit index.html: `<title>Edge Node 1 Cache - HIT</title>`)
2. **Edge-Node-2:**
   - IP: `10.0.2.10`, Mask: `255.255.255.0`, Gateway: `10.0.2.1`
   - Services -> HTTP: On
3. **Edge-Node-3:**
   - IP: `10.0.3.10`, Mask: `255.255.255.0`, Gateway: `10.0.3.1`
   - Services -> HTTP: On
4. **Origin-Server:**
   - IP: `10.0.100.10`, Mask: `255.255.255.0`, Gateway: `10.0.100.1`
   - Services -> HTTP: On (Edit index.html: `<title>Mock Authoritative Origin</title>`)
5. **PC-1:**
   - IP: `10.0.0.10`, Mask: `255.255.255.0`, Gateway: `10.0.0.1`

---

## 3. Verification Commands

### 1. Verify OSPF Routing Table on R1
```text
R1-Edge-Router# show ip route ospf
O    10.0.100.0/24 [110/2] via 10.0.10.2, 00:04:12, GigabitEthernet0/0/1
```

### 2. Verify Client Connectivity to Edge and Origin
From PC-1 Command Prompt:
```bash
ping 10.0.1.10   # Ping Edge Node 1 (Expect 4/4 replies < 1ms)
ping 10.0.100.10 # Ping Authoritative Origin (Expect 4/4 replies ~60ms)
```

### 3. Verify HTTP Request in Packet Tracer Web Browser
Open Desktop -> Web Browser on **PC-1**:
1. Navigate to `http://10.0.1.10` -> Displays **Edge Node 1 Cache - HIT**.
2. Switch to **Simulation Mode** in Packet Tracer (bottom right icon or `Shift + S`).
3. Click "Capture / Forward" to step through the packet:
   - Observe the TCP 3-Way Handshake (SYN, SYN-ACK, ACK).
   - Observe the HTTP GET request entering R1.
   - Observe 802.1Q tagging on subinterface `G0/0/2.10` entering `SW-Edge`.
   - Observe HTTP 200 OK returned to client.

---

## 4. Failure and Rerouting Simulation Demo
1. In Packet Tracer, click the red "X" (Delete tool) and disconnect the cable between `SW-Edge Fa0/1` and `Edge-Node-1`.
2. Notice the link goes red.
3. Open PC-1 Web Browser and request `http://10.0.2.10` (or VIP `10.0.0.1`).
4. Traffic is dynamically rerouted to Edge Node 2.
5. Reconnect the cable: Portfast restores connectivity within 2 seconds.
