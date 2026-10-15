#!/usr/bin/env python3
"""Local dev harness: backend + fake PVE + fake engine for manual/browser QA.
Nothing real is touched (fake engine stub, fake PVE HTTP server). Not a test.
Usage: python3 tests/backend/dev_harness.py [port]
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_backend_tests as t  # noqa: E402  (reuses its fake PVE + fixtures)

# importing the test module already started fake PVE + backend on random
# ports; re-serve the backend on a fixed port for the browser instead.
from http.server import ThreadingHTTPServer  # noqa: E402
import server as server_mod  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
httpd = ThreadingHTTPServer(("127.0.0.1", port),
                            server_mod.make_handler(t.BE))
print(f"UI at http://127.0.0.1:{port}/  (login admin@pve / pw-admin)",
      file=sys.stderr)
httpd.serve_forever()
