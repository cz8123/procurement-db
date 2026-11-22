"""在桌面创建两个启动快捷方式（.lnk）。

用法：
    python make_shortcuts.py            # 创建 / 覆盖
    python make_shortcuts.py --remove   # 删除

依赖 pywin32（已装在本项目 venv）。换机器重跑一次即可。
"""
import os
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "binaries", "python", "envs", "procurement", "Scripts", "python.exe",
)   # 仅作记录/备用；快捷方式本身直接指向 bat，bat 会动态定位 Python
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

SHORTCUTS = [
    {
        "name": "采购看板",
        "bat": "start-dashboard.bat",
        "desc": "采购分析看板（PRPO 数据，Power BI 首页复刻版）",
        "icon": CHROME + ",0",
    },
    # 注意：管理台（start-dbadmin.bat）在桌面已有「采购数据库管理」快捷方式，
    # 这里不再重复创建，避免桌面出现两个指向同一脚本的入口。
]


def desktop_dir() -> str:
    """桌面真实路径（可能被 OneDrive 重定向，优先读注册表）。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        val, _ = winreg.QueryValueEx(key, "Desktop")
    except OSError:
        val = os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.expandvars(val)


def main() -> int:
    import win32com.client

    desk = desktop_dir()
    shell = win32com.client.Dispatch("WScript.Shell")
    remove = "--remove" in sys.argv

    for s in SHORTCUTS:
        path = os.path.join(desk, s["name"] + ".lnk")
        if remove:
            if os.path.exists(path):
                os.remove(path)
                print("删除:", path)
            continue

        target = os.path.join(PROJ, s["bat"])
        if not os.path.exists(target):
            print("跳过（脚本不存在）:", target)
            continue

        lnk = shell.CreateShortCut(path)
        lnk.TargetPath = target
        lnk.WorkingDirectory = PROJ
        lnk.Description = s["desc"]
        lnk.IconLocation = s["icon"]
        lnk.WindowStyle = 1          # 1=正常窗口，能看到服务控制台
        lnk.Save()
        print("创建:", path, "->", target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
