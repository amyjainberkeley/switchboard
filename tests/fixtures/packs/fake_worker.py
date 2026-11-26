#!/usr/bin/python3 -IB
"""A stand-in for claude-mem's worker, for tests only: python3 fake_worker.py PORT [ok|hang|other].

ok     GET /api/health answers {"status": "ok", "port": PORT}, as claude-mem's worker does
hang   takes the connection and never answers
other  answers as some other program would
It listens on 127.0.0.1 only and stops on SIGTERM.
"""
import json
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
MODE = sys.argv[2] if len(sys.argv) > 2 else 'ok'


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if MODE == 'hang':
            time.sleep(3600)
            return
        body = json.dumps({'status': 'ok', 'uptime': 1, 'port': PORT} if MODE == 'ok' and self.path == '/api/health'
                          else {'hello': 'not claude-mem'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
HTTPServer(('127.0.0.1', PORT), H).serve_forever()
