# Packet Tracer Topology & Routing Architecture Demo

This document details the network topology, IP addressing plan, routing design, and device configurations for the **Adaptive Edge Caching** cluster simulation in **Cisco Packet Tracer 8.x**.

---

## 1. Network Topology Diagram

```mermaid
graph TD
    subgraph Clients["Client Subnet (10.0.0.0/24)"]
        PC1["Client PC-1<br/>10.0.0.10/24"]
        PC2["Client PC-2<br/>10.0.0.11/24"]
        SW_Client["Switch SW-Client<br/>Catalyst 2960"]
        PC1 --- SW_Client
        PC2 --- SW_Client
    end

    subgraph CoreRouting["Edge & Core Routing Infrastructure"]
        R1["Edge Router R1 (Cisco 4331)<br/>Traffic Controller Interceptor<br/>G0/0/0: 10.0.0.1 (Clients)<br/>G0/0/1: 10.0.10.1 (Transit to R2)<br/>G0/0/2: Subinterfaces (Edge Nodes)"]
        R2["Core Origin Router R2 (Cisco 4331)<br/>G0/0/0: 10.0.10.2 (Transit to R1)<br/>G0/0/1: 10.0.100.1 (Origin LAN)"]
        SW_Client ---|G0/1| R1
        R1 ---|Serial 0/1/0 or G0/0/1 (WAN Link)| R2
    end

    subgraph EdgeCluster["Edge Cache Server Farm (10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24)"]
        SW_Edge["Edge Switch SW-Edge<br/>Catalyst 3650 / 2960<br/>VLANs: 10, 20, 30"]
        R1 ---|802.1Q Trunk (G0/0/2)| SW_Edge
        
        Node1["Edge Node 1 Server<br/>10.0.1.10/24 (VLAN 10)<br/>Port 8081 / Peer 8080<br/>Weight Rank: Primary"]
        Node2["Edge Node 2 Server<br/>10.0.2.10/24 (VLAN 20)<br/>Port 8082 / Peer 8080<br/>Weight Rank: Secondary"]
        Node3["Edge Node 3 Server<br/>10.0.3.10/24 (VLAN 30)<br/>Port 8083 / Peer 8080<br/>Weight Rank: Backup"]

        SW_Edge ---|Fa0/1 (VLAN 10)| Node1
        SW_Edge ---|Fa0/2 (VLAN 20)| Node2
        SW_Edge ---|Fa0/3 (VLAN 30)| Node3
        
        Node1 -.-|P2P Cache Sync| Node2
        Node2 -.-|P2P Cache Sync| Node3
        Node1 -.-|P2P Cache Sync| Node3
    end

    subgraph OriginDC["Authoritative Data Center (10.0.100.0/24)"]
        SW_Origin["Origin Switch SW-Origin"]
        R2 ---|G0/0/1| SW_Origin
        OriginServer["Authoritative Mock Origin<br/>10.0.100.10/24<br/>Port 8000<br/>Simulated Delay: 60ms"]
        SW_Origin --- OriginServer
    end
```

---

## 2. IP Addressing Plan

| Device | Interface | IP Address | Subnet Mask | Default Gateway | Purpose |
|---|---|---|---|---|---|
| **PC-1** | FastEthernet0 | `10.0.0.10` | `255.255.255.0` | `10.0.0.1` | Client testing workstation 1 |
| **PC-2** | FastEthernet0 | `10.0.0.11` | `255.255.255.0` | `10.0.0.1` | Client testing workstation 2 |
| **Router R1** | G0/0/0 | `10.0.0.1` | `255.255.255.0` | N/A | Client LAN Gateway |
| **Router R1** | G0/0/1 | `10.0.10.1` | `255.255.255.252` | N/A | WAN Transit link to Core Router R2 |
| **Router R1** | G0/0/2.10 | `10.0.1.1` | `255.255.255.0` | N/A | VLAN 10 Subinterface (Edge Node 1 Gateway) |
| **Router R1** | G0/0/2.20 | `10.0.2.1` | `255.255.255.0` | N/A | VLAN 20 Subinterface (Edge Node 2 Gateway) |
| **Router R1** | G0/0/2.30 | `10.0.3.1` | `255.255.255.0` | N/A | VLAN 30 Subinterface (Edge Node 3 Gateway) |
| **Router R2** | G0/0/0 | `10.0.10.2` | `255.255.255.252` | N/A | WAN Transit link to Router R1 |
| **Router R2** | G0/0/1 | `10.0.100.1` | `255.255.255.0` | N/A | Origin Server LAN Gateway |
| **Edge-Node-1**| FastEthernet0 | `10.0.1.10` | `255.255.255.0` | `10.0.1.1` | Edge Proxy Node 1 (Adaptive Cache) |
| **Edge-Node-2**| FastEthernet0 | `10.0.2.10` | `255.255.255.0` | `10.0.2.1` | Edge Proxy Node 2 (Adaptive Cache) |
| **Edge-Node-3**| FastEthernet0 | `10.0.3.10` | `255.255.255.0` | `10.0.3.1` | Edge Proxy Node 3 (Adaptive Cache) |
| **OriginServer**| FastEthernet0| `10.0.100.10` | `255.255.255.0` | `10.0.100.1`| Authoritative Origin Server |

---

## 3. Dynamic Routing & Traffic Control Design

1. **OSPF Dynamic Routing:**
   - Single Area OSPF (Area 0) is configured between R1 and R2.
   - Passive interfaces are applied on edge server and client facing ports to avoid OSPF hello storm.
2. **Traffic Management Controller Role:**
   - Intercepts requests on VIP `10.0.0.1:8000` (or running inside R1 / Edge Node 1).
   - Evaluates dynamic weights ($\alpha \cdot L + \beta \cdot P + \gamma \cdot U + \delta \cdot Q$).
   - Forwards client HTTP sessions to `10.0.1.10`, `10.0.2.10`, or `10.0.3.10`.
3. **Peer-to-Peer Inter-Node Connectivity:**
   - R1 routes inter-VLAN peer traffic between `10.0.1.0/24`, `10.0.2.0/24`, and `10.0.3.0/24` with sub-millisecond switching.
