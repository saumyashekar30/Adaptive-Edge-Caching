"""Tiny server that echoes back all received headers as JSON."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        hdrs = {k: v for k, v in self.headers.items()}
        payload = json.dumps(hdrs, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *a): pass

if __name__ == "__main__":
    s = HTTPServer(("127.0.0.1", 7777), EchoHandler)
    print("Header echo on :7777")
    s.handle_request()  # serve one request then exit
