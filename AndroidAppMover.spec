# -*- mode: python ; coding: utf-8 -*-
"""打包独立 Mac 应用，包含实际使用的 Qt 组件、ADB 及许可证。"""

from pathlib import Path
import sys
import subprocess
from importlib.metadata import distribution

from PyInstaller.config import CONF
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtCore import Qt, QRectF

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from hoardy_adb.apk import find_adb, find_aapt2
import shutil

adb = Path(find_adb())
notice = adb.parent / "NOTICE.txt"
data = [(str(root / "LICENSE.txt"), "licenses")]
if notice.exists():
    data.append((str(notice), "platform-tools"))

# 随包携带运行依赖的现有许可文件；Qt/PySide 在本 GPLv3 项目中采用 GPLv3 选项。
for package in ("apkutils", "kisstdlib", "cffi", "pycparser", "sortedcontainers", "beautifulsoup4",
                "lxml", "pyftype", "Pygments", "click", "soupsieve", "typing_extensions"):
    dependency = distribution(package)
    for filename in dependency.files or []:
        if any(part.lower().startswith(("license", "copying", "notice")) for part in filename.parts):
            source = dependency.locate_file(filename)
            if source.is_file():
                data.append((str(source), "licenses/" + package))
third_party = Path(CONF["workpath"]) / "ThirdPartyNotices.txt"
third_party.parent.mkdir(parents=True, exist_ok=True)
third_party.write_text(
    "Android App Mover / hoardy-adb: GPL-3.0-or-later. See LICENSE.txt.\n"
    "Qt, PySide6 and Shiboken6: GPL-3.0 option; see LICENSE.txt.\n"
    "Qt source: https://code.qt.io/cgit/qt/\n"
    "PySide/Shiboken source: https://code.qt.io/cgit/pyside/pyside-setup.git/\n"
    "Android Debug Bridge: see platform-tools/NOTICE.txt.\n"
    "Other runtime dependency licenses are included in this directory.\n",
    encoding="utf-8",
)
data.append((str(third_party), "licenses"))

# AAPT2 作为独立资源工具随包携带；保留其架构，不能按主程序架构强行裁剪。
# 本机已有工具能显著加速资源解析，其他电脑不能运行它时会回退 Python。
aapt2 = find_aapt2()
if aapt2:
    copied = Path(CONF["workpath"]) / "native-tools/aapt2"
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(aapt2, copied)
    subprocess.run(["codesign", "--force", "--sign", "-", str(copied)], check=True)
    data.append((str(copied), "tools/aapt2"))
    source_notice = Path(aapt2).parent / "NOTICE.txt"
    if source_notice.exists():
        data.append((str(source_notice), "tools/aapt2"))

# 图标属于正式打包资源，按尺寸生成到构建目录，不在仓库加入重复位图文件。
iconset = Path(CONF["workpath"]) / "AndroidAppMover.iconset"
iconset.mkdir(parents=True, exist_ok=True)
for logical in (16, 32, 128, 256, 512):
    for multiplier in (1, 2):
        size = logical * multiplier
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 512, size / 512)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#087cff"))
        painter.drawRoundedRect(QRectF(16, 16, 480, 480), 108, 108)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(QRectF(104, 120, 122, 256), 24, 24)
        painter.drawRoundedRect(QRectF(286, 136, 122, 256), 24, 24)
        painter.setPen(QPen(QColor("#087cff"), 12))
        painter.drawLine(142, 344, 188, 344)
        painter.drawLine(324, 360, 370, 360)
        painter.setPen(QPen(QColor("white"), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(236, 256, 272, 256)
        painter.drawLine(254, 238, 272, 256)
        painter.drawLine(254, 274, 272, 256)
        painter.end()
        suffix = "@2x" if multiplier == 2 else ""
        image.save(str(iconset / f"icon_{logical}x{logical}{suffix}.png"))
icon = iconset.with_suffix(".icns")
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icon)], check=True)

a = Analysis(
    [str(root / "hoardy_adb" / "gui.py")],
    pathex=[str(root)],
    binaries=[(str(adb), "platform-tools")],
    datas=data,
    hiddenimports=["apkutils"],
    excludes=["tkinter", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AndroidAppMover",
          console=False, target_arch="arm64", codesign_identity=None)
# 收集中间目录放构建区；正式应用与 ZIP 始终输出到项目 release，不受调用目录影响。
CONF["distpath"] = str(Path(CONF["workpath"]) / "collect")
collection = COLLECT(exe, a.binaries, a.datas, name="AndroidAppMover")
CONF["distpath"] = str(root / "release")
app = BUNDLE(collection, name="安卓应用搬家.app", icon=str(icon), bundle_identifier="io.github.wx383847364.hoardy-adb",
             info_plist={"CFBundleDisplayName": "安卓应用搬家", "CFBundleShortVersionString": "0.3.3",
                         "NSHighResolutionCapable": True})

# 打包入口同时更新可下载压缩包，保留应用结构、符号链接和签名资源。
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app.name)], check=True)
archive = root / "release/Android-App-Mover-macOS-arm64.zip"
archive.unlink(missing_ok=True)
subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app.name), str(archive)], check=True)
