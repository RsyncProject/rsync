#!/usr/bin/env python3

from harness.rsync import claim_ports, make_proxy_server, require_tcp, run_proxy_probe

cases = (
    (12931, 'a' * 1500 + '.invalid', b'', 'proxy CONNECT request too long'),
    (12932, 'example.invalid', b'HTTP/1.0 200 OK\r\n' + b'X' * 1023,
     'proxy response header line too long'),
    (12873, 'example.invalid', b'X' * 1023, 'proxy response line too long'),
)
require_tcp('proxy tests require TCP')
claim_ports(*(port for port, _, _, _ in cases))

for port, host, response, error in cases:
    make_proxy_server(port, response)
    run_proxy_probe(port, host, error)

print('oversized proxy requests and responses are rejected')
