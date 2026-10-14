"""Generates binary libpcap (.pcap) files demonstrating edge caching network flows:

1. cache_hit_edge_serve.pcap: Sub-millisecond hit retrieval with X-Cache: HIT
2. cache_miss_origin_fetch.pcap: Cold miss showing proxy-to-origin retrieval with X-Cache: MISS
3. peer_cache_fetch.pcap: Peer cache query showing GET /peer-cache/ and X-Peer-Cache: HIT
4. node_failure_retransmission_reroute.pcap: Packet loss, TCP retransmission, and failover reroute
"""

from __future__ import annotations

import socket
import struct
import time
from pathlib import Path


def ip_checksum(data: bytes) -> int:
    """Compute standard 16-bit Internet checksum."""
    if len(data) % 2 == 1:
        data += b"\x00"
    s = sum(struct.unpack(f"!{len(data)//2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def tcp_checksum(src_ip: str, dst_ip: str, tcp_segment: bytes) -> int:
    """Compute TCP checksum including pseudo-header."""
    src_bytes = socket.inet_aton(src_ip)
    dst_bytes = socket.inet_aton(dst_ip)
    length = len(tcp_segment)
    pseudo_header = src_bytes + dst_bytes + struct.pack("!BBH", 0, socket.IPPROTO_TCP, length)
    return ip_checksum(pseudo_header + tcp_segment)


def build_packet(
    src_mac: bytes,
    dst_mac: bytes,
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    payload: bytes = b"",
    window: int = 64240,
) -> bytes:
    """Construct an Ethernet/IPv4/TCP packet."""
    eth_header = dst_mac + src_mac + struct.pack("!H", 0x0800)  # EtherType IPv4

    ip_tot_len = 20 + 20 + len(payload)
    ip_header_pre = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, ip_tot_len, 54321, 0x4000, 64, socket.IPPROTO_TCP, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip))
    ip_cksum = ip_checksum(ip_header_pre)
    ip_header = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, ip_tot_len, 54321, 0x4000, 64, socket.IPPROTO_TCP, ip_cksum,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip))

    data_offset_val = (5 << 4)
    tcp_pre = struct.pack("!HHIIBBHHH",
        src_port, dst_port, seq, ack, data_offset_val, flags, window, 0, 0)
    tcp_cksum = tcp_checksum(src_ip, dst_ip, tcp_pre + payload)
    tcp_header = struct.pack("!HHIIBBHHH",
        src_port, dst_port, seq, ack, data_offset_val, flags, window, tcp_cksum, 0)

    return eth_header + ip_header + tcp_header + payload


class PcapWriter:
    """Writes standard 24-byte header and timestamped packet records."""

    def __init__(self, filename: Path | str) -> None:
        self.filename = Path(filename)
        self.packets = []

    def add_packet(self, raw_pkt: bytes, timestamp_sec: float) -> None:
        sec = int(timestamp_sec)
        usec = int((timestamp_sec - sec) * 1e6)
        length = len(raw_pkt)
        header = struct.pack("<IIII", sec, usec, length, length)
        self.packets.append(header + raw_pkt)

    def write(self) -> None:
        global_header = struct.pack("<IHHiIII",
            0xA1B2C3D4,  # magic number
            2, 4,        # major, minor
            0, 0,        # thiszone, sigfigs
            65535,       # snaplen
            1,           # LINKTYPE_ETHERNET
        )
        with open(self.filename, "wb") as f:
            f.write(global_header)
            for pkt in self.packets:
                f.write(pkt)


def generate_all_pcaps(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = 1776123400.0  # reference epoch

    mac_client = bytes.fromhex("001122334455")
    mac_edge1 = bytes.fromhex("00AABBCCDDEE")
    mac_edge2 = bytes.fromhex("00AABBCCDDFF")
    mac_origin = bytes.fromhex("00FFEECCBBAA")

    ip_client = "10.0.0.10"
    ip_edge1 = "10.0.1.10"
    ip_edge2 = "10.0.2.10"
    ip_origin = "10.0.100.10"

    # TCP Flags
    SYN, ACK, PSH = 0x02, 0x10, 0x08
    FIN, RST = 0x01, 0x04

    # --------------------------------------------------------------------------
    # 1. cache_hit_edge_serve.pcap
    # --------------------------------------------------------------------------
    pcap_hit = PcapWriter(output_dir / "cache_hit_edge_serve.pcap")
    t = t0
    # SYN
    pcap_hit.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 51000, 8081, 1000, 0, SYN), t)
    # SYN-ACK
    t += 0.0002
    pcap_hit.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 51000, 5000, 1001, SYN | ACK), t)
    # ACK
    t += 0.0001
    pcap_hit.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 51000, 8081, 1001, 5001, ACK), t)
    # HTTP GET
    t += 0.0001
    http_req = b"GET /data/stream-101 HTTP/1.1\r\nHost: edge-node-1\r\nUser-Agent: curl/8.4\r\nAccept: */*\r\n\r\n"
    pcap_hit.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 51000, 8081, 1001, 5001, PSH | ACK, http_req), t)
    # HTTP 200 OK (Served in 0.45ms!)
    t += 0.00045
    http_resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: 1024\r\n"
        b"X-Cache: HIT\r\n"
        b"X-Edge-Node-Id: edge-node-1\r\n"
        b"X-Cache-Lookup-Time-Ms: 0.42\r\n"
        b"X-Response-Time-Ms: 0.85\r\n"
        b"X-Resource-Version: 1\r\n"
        b"ETag: \"stream-101-v1\"\r\n\r\n"
        + b"A" * 1024
    )
    pcap_hit.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 51000, 5001, 1001 + len(http_req), PSH | ACK, http_resp), t)
    # Client ACK
    t += 0.0002
    pcap_hit.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 51000, 8081, 1001 + len(http_req), 5001 + len(http_resp), ACK), t)
    pcap_hit.write()

    # --------------------------------------------------------------------------
    # 2. cache_miss_origin_fetch.pcap
    # --------------------------------------------------------------------------
    pcap_miss = PcapWriter(output_dir / "cache_miss_origin_fetch.pcap")
    t = t0 + 10.0
    # Client -> Proxy Handshake
    pcap_miss.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 52000, 8081, 2000, 0, SYN), t)
    t += 0.0002
    pcap_miss.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 52000, 6000, 2001, SYN | ACK), t)
    t += 0.0001
    pcap_miss.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 52000, 8081, 2001, 6001, ACK), t)
    # Client HTTP GET
    t += 0.0001
    req_body = b"GET /data/new-video-505 HTTP/1.1\r\nHost: edge-node-1\r\n\r\n"
    pcap_miss.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 52000, 8081, 2001, 6001, PSH | ACK, req_body), t)

    # Edge Proxy -> Origin Upstream Handshake
    t += 0.0005
    pcap_miss.add_packet(build_packet(mac_edge1, mac_origin, ip_edge1, ip_origin, 45000, 8000, 8000, 0, SYN), t)
    t += 0.0150  # Propagation delay to origin
    pcap_miss.add_packet(build_packet(mac_origin, mac_edge1, ip_origin, ip_edge1, 8000, 45000, 9000, 8001, SYN | ACK), t)
    t += 0.0150
    pcap_miss.add_packet(build_packet(mac_edge1, mac_origin, ip_edge1, ip_origin, 45000, 8000, 8001, 9001, ACK), t)

    # Edge Proxy Upstream Request
    t += 0.0002
    origin_req = b"GET /data/new-video-505 HTTP/1.1\r\nHost: origin-server\r\n\r\n"
    pcap_miss.add_packet(build_packet(mac_edge1, mac_origin, ip_edge1, ip_origin, 45000, 8000, 8001, 9001, PSH | ACK, origin_req), t)

    # Origin Response after 60ms simulated processing
    t += 0.0600
    origin_resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: 2048\r\n"
        b"X-Origin-Latency-Ms: 60.10\r\n"
        b"ETag: \"new-video-505-v1\"\r\n\r\n"
        + b"B" * 2048
    )
    pcap_miss.add_packet(build_packet(mac_origin, mac_edge1, ip_origin, ip_edge1, 8000, 45000, 9001, 8001 + len(origin_req), PSH | ACK, origin_resp), t)

    # Proxy responds to Client with X-Cache: MISS
    t += 0.0010
    edge_resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: 2048\r\n"
        b"X-Cache: MISS\r\n"
        b"X-Edge-Node-Id: edge-node-1\r\n"
        b"X-Origin-Latency-Ms: 60.10\r\n"
        b"X-Response-Time-Ms: 62.15\r\n"
        b"ETag: \"new-video-505-v1\"\r\n\r\n"
        + b"B" * 2048
    )
    pcap_miss.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 52000, 6001, 2001 + len(req_body), PSH | ACK, edge_resp), t)
    pcap_miss.write()

    # --------------------------------------------------------------------------
    # 3. peer_cache_fetch.pcap
    # --------------------------------------------------------------------------
    pcap_peer = PcapWriter(output_dir / "peer_cache_fetch.pcap")
    t = t0 + 20.0
    # Client -> Node 2
    req_node2 = b"GET /data/stream-101 HTTP/1.1\r\nHost: edge-node-2\r\n\r\n"
    pcap_peer.add_packet(build_packet(mac_client, mac_edge2, ip_client, ip_edge2, 53000, 8082, 3000, 7000, PSH | ACK, req_node2), t)

    # Node 2 -> Node 1: GET /peer-cache/stream-101
    t += 0.0003
    peer_req = b"GET /peer-cache/stream-101 HTTP/1.1\r\nHost: edge-node-1\r\nX-Requester-Node-Id: edge-node-2\r\n\r\n"
    pcap_peer.add_packet(build_packet(mac_edge2, mac_edge1, ip_edge2, ip_edge1, 46000, 8081, 4000, 8000, PSH | ACK, peer_req), t)

    # Node 1 -> Node 2: 200 OK (X-Peer-Cache: HIT)
    t += 0.0012
    peer_resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: 1024\r\n"
        b"X-Peer-Cache: HIT\r\n"
        b"X-Peer-Node-Id: edge-node-1\r\n"
        b"X-Resource-Version: 1\r\n\r\n"
        + b"A" * 1024
    )
    pcap_peer.add_packet(build_packet(mac_edge1, mac_edge2, ip_edge1, ip_edge2, 8081, 46000, 8000, 4000 + len(peer_req), PSH | ACK, peer_resp), t)

    # Node 2 -> Client: 200 OK (X-Cache: PEER_HIT)
    t += 0.0005
    client_ret = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: 1024\r\n"
        b"X-Cache: PEER_HIT\r\n"
        b"X-Edge-Node-Id: edge-node-2\r\n"
        b"X-Peer-Node-Id: edge-node-1\r\n"
        b"X-Peer-Cache: HIT\r\n"
        b"X-Response-Time-Ms: 2.10\r\n\r\n"
        + b"A" * 1024
    )
    pcap_peer.add_packet(build_packet(mac_edge2, mac_client, ip_edge2, ip_client, 8082, 53000, 7000, 3000 + len(req_node2), PSH | ACK, client_ret), t)
    pcap_peer.write()

    # --------------------------------------------------------------------------
    # 4. node_failure_retransmission_reroute.pcap
    # --------------------------------------------------------------------------
    pcap_fail = PcapWriter(output_dir / "node_failure_retransmission_reroute.pcap")
    t = t0 + 30.0
    # Client sends SYN to Edge Node 2 (which is dead)
    pcap_fail.add_packet(build_packet(mac_client, mac_edge2, ip_client, ip_edge2, 54000, 8082, 9000, 0, SYN), t)

    # RTO Retransmission 1 (1.0s later)
    t += 1.0000
    pcap_fail.add_packet(build_packet(mac_client, mac_edge2, ip_client, ip_edge2, 54000, 8082, 9000, 0, SYN), t)

    # RTO Retransmission 2 (2.0s later)
    t += 2.0000
    pcap_fail.add_packet(build_packet(mac_client, mac_edge2, ip_client, ip_edge2, 54000, 8082, 9000, 0, SYN), t)

    # Client aborts connection with RST
    t += 0.2000
    pcap_fail.add_packet(build_packet(mac_client, mac_edge2, ip_client, ip_edge2, 54000, 8082, 9001, 0, RST), t)

    # Traffic Controller detects failover and redirects client to Edge Node 1
    t += 0.0500
    pcap_fail.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 54001, 8081, 10000, 0, SYN), t)
    t += 0.0002
    pcap_fail.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 54001, 20000, 10001, SYN | ACK), t)
    t += 0.0001
    pcap_fail.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 54001, 8081, 10001, 20001, ACK), t)

    # Rerouted Request succeeded on Node 1
    t += 0.0001
    rerouted_req = b"GET /data/stream-101 HTTP/1.1\r\nHost: edge-node-1\r\nX-Failover-Attempt: 2\r\n\r\n"
    pcap_fail.add_packet(build_packet(mac_client, mac_edge1, ip_client, ip_edge1, 54001, 8081, 10001, 20001, PSH | ACK, rerouted_req), t)
    t += 0.0008
    rerouted_resp = (
        b"HTTP/1.1 200 OK\r\n"
        b"X-Cache: HIT\r\n"
        b"X-Selected-Node: edge-node-1\r\n"
        b"X-Failover: true\r\n\r\n"
        + b"A" * 512
    )
    pcap_fail.add_packet(build_packet(mac_edge1, mac_client, ip_edge1, ip_client, 8081, 54001, 20001, 10001 + len(rerouted_req), PSH | ACK, rerouted_resp), t)
    pcap_fail.write()

    print(f"[*] Generated 4 binary PCAP captures in {output_dir}")


if __name__ == "__main__":
    generate_all_pcaps(Path(__file__).resolve().parent)
