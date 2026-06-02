#!/usr/bin/env python3
"""
Stream backend.log over HTTP. Run on the TAM server, curl from dev machine.

Usage:
    python3 stream-log.py [PORT] [LOG_PATH]

Defaults:
    PORT=9876
    LOG_PATH=/data/dataiku/dss_data/run/backend.log
"""
import http.server
import os
import sys
import time
import signal
import subprocess

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9876
LOG_PATH = sys.argv[2] if len(sys.argv) > 2 else "/data/dataiku/dss_data/run/backend.log"


class LogStreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not os.path.exists(LOG_PATH):
            self.send_error(404, f"Log file not found: {LOG_PATH}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        proc = subprocess.Popen(
            ["tail", "-n", "0", "-f", LOG_PATH],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                chunk = f"{len(line):x}\r\n".encode() + line + b"\r\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()
            proc.wait()

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[stream-log] {args[0]}\n")


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), LogStreamHandler)
    signal.signal(signal.SIGINT, lambda *_: (server.shutdown(), sys.exit(0)))
    hostname = os.popen("hostname -f 2>/dev/null || hostname").read().strip()
    print(f"Streaming {LOG_PATH}")
    print(f"Listening on http://{hostname}:{PORT}/")
    print(f"Ctrl+C to stop\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
