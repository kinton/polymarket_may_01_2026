#!/usr/bin/env python3
"""List all ClobClient methods"""
from py_clob_client.client import ClobClient

methods = [m for m in dir(ClobClient) if not m.startswith('_')]
print('All ClobClient public methods:\n')
for m in sorted(methods):
    print(f'  {m}')
