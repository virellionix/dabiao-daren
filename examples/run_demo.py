#!/usr/bin/env python3
"""Run the complete offline shared-entry demo in one command."""

import json

from shared_entry_demo import demo


if __name__ == "__main__":
    result = demo()
    print(json.dumps({
        "message": "演示完成：旧式中文列 → 统一记录 → 授权上下文补查 → 标签/证据/复核状态 → 旧列导出",
        "network": False,
        "accuracy_claim": False,
        "result": result,
    }, ensure_ascii=False, indent=2))
