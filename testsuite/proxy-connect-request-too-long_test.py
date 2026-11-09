#!/usr/bin/env python3
from rsyncfns import claim_ports, require_tcp
from rsyncfns import make_proxy_server, run_proxy_probe

PORT = 12931
require_tcp("fake-proxy listener needs a real TCP socket; run with --use-tcp")
claim_ports(PORT)
make_proxy_server(PORT, b"")
run_proxy_probe(PORT, 'a' * 1500 + '.invalid', 'proxy CONNECT request too long')
print("proxy-connect-request-too-long: oversized CONNECT request rejected before write")
