# -*- coding: utf-8 -*-
import os
import sys

print("hello from script-server")
print("args:", sys.argv[1:])
print("env DEMO:", os.environ.get("DEMO", ""))
sys.exit(0)