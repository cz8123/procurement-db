# -*- coding: utf-8 -*-
"""无头 UI 测试用的隔离实例：复制真实库到 tests/_tmp_agg_ui.db 后启动 5001。

跑法（先起本文件，再跑 node tests/agg_check.js）：
    python tests/_agg_server.py
"""
import os
import sys
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_agg_ui.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import app  # noqa: E402

if __name__ == "__main__":
    # 模板改动即时生效（省得每改一次前端就重启一次测试实例）
    app.app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.app.jinja_env.auto_reload = True
    app.app.run(host="127.0.0.1", port=5001, threaded=True, use_reloader=False)
