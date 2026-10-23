"""Quick diagnostic to test MISS->HIT directly (no PowerShell headers involved)."""
import json
import time
import urllib.request

ORIGIN  = "http://127.0.0.1:8000"
NODE1   = "http://127.0.0.1:8081"
NODE2   = "http://127.0.0.1:8082"

def fetch(url, label=""):
    """Fetch a URL and return (status, headers_dict, body)."""
    req = urllib.request.Request(url, headers={"User-Agent": "DemoTest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            hdrs = {k: v for k, v in resp.headers.items()}
            print(f"  [{label}]  HTTP {resp.status}  "
                  f"X-Cache={hdrs.get('X-Cache','N/A')}  "
                  f"X-Peer-Node-Id={hdrs.get('X-Peer-Node-Id','N/A')}  "
                  f"X-Resource-Version={hdrs.get('X-Resource-Version','N/A')}  "
                  f"size={len(body)}")
            return resp.status, hdrs, body
    except Exception as e:
        print(f"  [{label}]  FAILED: {e}")
        return None, None, None

def stats(node_url):
    """Print cache stats for a node."""
    try:
        with urllib.request.urlopen(f"{node_url}/cache/stats", timeout=3) as resp:
            data = json.loads(resp.read())
            print(f"  Stats: hits={data.get('hits')} misses={data.get('misses')} "
                  f"expirations={data.get('expirations')} objects={data.get('object_count')} "
                  f"policy={data.get('policy')}")
            return data
    except Exception as e:
        print(f"  Stats FAILED: {e}")
        return None

def main():
    resource = f"diag-{int(time.time())}"
    print(f"\n{'='*60}")
    print(f"Diagnostic Test  |  resource = {resource}")
    print(f"{'='*60}\n")

    # --- Health checks ---
    print("[1] Health checks")
    for name, url in [("Origin", ORIGIN), ("Node1", NODE1), ("Node2", NODE2)]:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                d = json.loads(r.read())
                print(f"  {name}: {d.get('status','?')}")
        except Exception as e:
            print(f"  {name}: DOWN ({e})")
            if name in ("Origin", "Node1"):
                print("  Cannot continue without Origin + Node1")
                return

    # --- Demo 2: MISS then HIT ---
    print(f"\n[2] Demo 2 — MISS then HIT (Node1, /data/{resource})")
    print("  Stats BEFORE:")
    stats(NODE1)

    print(f"\n  Request 1 (expect MISS):")
    s1, h1, _ = fetch(f"{NODE1}/data/{resource}", "REQ-1")

    print(f"\n  Stats AFTER request 1:")
    stats(NODE1)

    # Tiny pause to ensure no race condition
    time.sleep(0.1)

    print(f"\n  Request 2 (expect HIT):")
    s2, h2, _ = fetch(f"{NODE1}/data/{resource}", "REQ-2")

    print(f"\n  Stats AFTER request 2:")
    s2_stats = stats(NODE1)

    # Verdict
    c1 = h1.get("X-Cache") if h1 else "FAIL"
    c2 = h2.get("X-Cache") if h2 else "FAIL"
    if c1 == "MISS" and c2 == "HIT":
        print(f"\n  ** PASS: MISS -> HIT **")
    else:
        print(f"\n  ** FAIL: expected MISS->HIT but got {c1}->{c2} **")
        if c2 == "MISS" and s2_stats and s2_stats.get("expirations", 0) > 0:
            print("  DIAGNOSIS: TTL expiration killed the entry between requests.")
            print("  The cache_store.default_ttl_seconds or origin max-age is likely too short,")
            print("  or the CacheEntry.is_expired() check is using the wrong clock.")
        elif c2 == "BYPASS":
            print("  DIAGNOSIS: Client sent Cache-Control: no-cache header → bypassed cache.")

    # --- Demo 3: PEER_HIT ---
    print(f"\n[3] Demo 3 — PEER_HIT (Node2 fetches from Node1's cache)")
    print(f"  Requesting /data/{resource} via Node2 (expect PEER_HIT):")
    s3, h3, _ = fetch(f"{NODE2}/data/{resource}", "PEER")
    c3 = h3.get("X-Cache") if h3 else "FAIL"
    if c3 == "PEER_HIT":
        print(f"  ** PASS: PEER_HIT (served by {h3.get('X-Peer-Node-Id')}) **")
    else:
        print(f"  ** Got {c3} instead of PEER_HIT **")

    print(f"\n{'='*60}")
    print("Diagnostic complete")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
