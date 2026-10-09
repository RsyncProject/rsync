#!/usr/bin/env python3
from rsyncfns import claim_ports, require_tcp
from rsyncfns import make_proxy_server, run_proxy_probe

PORT = 12932
require_tcp("fake-proxy listener needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)
make_proxy_server(PORT, b"HTTP/1.0 200 OK\r\n" + b"X" * 1023)
run_proxy_probe(PORT, 'example.invalid', 'proxy response header line too long')
print("proxy-response-header-too-long: oversized proxy header line rejected")
