# -*- coding: utf-8 -*-
"""示例：业务脚本如何接收调度平台（/run）传来的参数。

平台侧 /run 的 body 三个字段会落到脚本里：
  args  -> 命令行参数 -> sys.argv[1:]
  env   -> 环境变量   -> os.environ
  (file_path 决定跑哪个脚本，不是参数)

支持三种写法，本脚本都解析：
  --key 123           （空格分隔的开关参数）
  key=123             （等号形式，等价于 query string）
  order=777           （同上，任意 key）
"""
import json
import os
import sys


def parse(argv):
    """同时支持 --key value / --key=value / key=value / 裸位置参数。"""
    out = {}
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a[2:].split("=", 1)
                out[k] = v
            elif i + 1 < len(argv):
                out[a[2:]] = argv[i + 1]
                i += 1
            else:
                out[a[2:]] = ""
        elif "=" in a:
            k, v = a.split("=", 1)
            out[k] = v
        else:
            positional.append(a)
        i += 1
    return out, positional


if __name__ == "__main__":
    params, rest = parse(sys.argv[1:])

    print("python     :", sys.executable)
    print("cwd        :", os.getcwd())
    print("argv       :", json.dumps(sys.argv[1:], ensure_ascii=False))
    print("params     :", json.dumps(params, ensure_ascii=False, sort_keys=True))
    print("positional :", json.dumps(rest, ensure_ascii=False))
    print("key        :", params.get("key"))
    print("env key    :", os.environ.get("key"))      # Windows 上键会自动大写，取小写也能命中
    print("env KEY    :", os.environ.get("KEY"))
    print("env ORDER_ID:", os.environ.get("ORDER_ID", ""))
    print("env BIZ_DATE:", os.environ.get("BIZ_DATE", ""))
    # 下面两个不是平台传的，是子进程从 Windows（服务账户）继承来的环境变量
    print("env 总数   :", len(os.environ))
    print("继承 Windows:", "USERNAME=" + os.environ.get("USERNAME", "?"),
          "COMPUTERNAME=" + os.environ.get("COMPUTERNAME", "?"))
    # 平台 token 不应出现在业务脚本环境里（执行器会剥掉 WF_SCRIPT_*）
    print("token leaked:", any(k.upper().startswith("WF_SCRIPT_") for k in os.environ))
    sys.exit(0)
