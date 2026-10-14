# Wireshark Network Traces & Packet Captures

This directory contains valid binary libpcap `.pcap` files and packet analysis notes validating the Adaptive Edge Caching protocol stack.

## Files in this Directory

- `cache_hit_edge_serve.pcap`: Binary capture of client HTTP request resulting in a 0.45ms local edge cache hit (`X-Cache: HIT`).
- `cache_miss_origin_fetch.pcap`: Binary capture of cold cache miss triggering proxy upstream retrieval from origin (60ms delay) and edge caching (`X-Cache: MISS`).
- `peer_cache_fetch.pcap`: Binary capture of horizontal peer fetch between sibling edge nodes (`GET /peer-cache/{id}`) returning `X-Peer-Cache: HIT`.
- `node_failure_retransmission_reroute.pcap`: Binary capture showing node failure, TCP SYN retransmission timeout, RST abort, and immediate failover reroute to alternate edge node.
- `generate_pcaps.py`: Python generator used to reproduce the raw libpcap captures.
- `wireshark_analysis.md`: Detailed frame-by-frame protocol inspection, sequence number timelines, and display filter guide.
