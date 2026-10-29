"""安卓设备、APK 导出和安装的共享逻辑，不依赖图形界面。"""

from __future__ import annotations

import dataclasses
import base64
import datetime
import hashlib
import io
import json
import logging
import plistlib
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import uuid
import zipfile
import xml.etree.ElementTree as ET

PACKAGE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*\Z")
Notify = Callable[[dict], None]
LOG = logging.getLogger("hoardy_adb")
LOG_PATH = (Path.home() / "Library/Logs/HoardyADB" if sys.platform == "darwin"
            else Path.home() / ".local/state/hoardy-adb") / "AndroidAppMover.log"


def configure_logging(path: Path | None = None) -> Path:
    """配置最多四份、每份 2 MB 的本地滚动日志；不记录应用清单或 APK 内容。"""
    destination = path or LOG_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == destination.resolve()
               for handler in LOG.handlers):
        handler = RotatingFileHandler(destination, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    LOG.info("Application logging started; file=%s", destination)
    return destination


def usb_diagnostic(serial: str) -> str:
    """Mac 上只读取指定手机的 USB 状态，区分线缆未连接与手机没有暴露调试接口。"""
    if sys.platform != "darwin" or not serial:
        return ""
    try:
        result = subprocess.run(["ioreg", "-a", "-r", "-c", "IOUSBHostDevice"],
                                capture_output=True, timeout=5, check=True)
        for device in plistlib.loads(result.stdout):
            identifier = device.get("USB Serial Number") or device.get("kUSBSerialNumberString")
            if identifier != serial:
                continue
            # IORegistry 的签名包含 USB 接口类：FF / 42 / 01 是 ADB 调试接口。
            signature = device.get("UsbDeviceSignature")
            if not isinstance(signature, bytes):
                return "USB 已连接，暂时无法读取调试接口状态，请检查手机上的 USB 调试授权。"
            if b"\xff\x42\x01" not in signature:
                return "USB 已连接，但手机未提供 ADB 调试接口。请检查 USB 调试开关和 USB 连接模式。"
            return "USB 调试接口仍在，但 ADB 通道暂不可用。请保持解锁并检查手机上的授权提示。"
        return "未检测到原手机的 USB 连接，请检查数据线、接口和手机连接状态。"
    except (OSError, ValueError, subprocess.SubprocessError):
        LOG.exception("Could not inspect USB interface for target=%s", serial)
        return ""


def explain_error(message: str) -> str:
    """为常见手机错误添加中文原因，同时保留原始信息便于排查。"""
    reasons = {
        "INSTALL_FAILED_NO_MATCHING_ABIS": "该应用不支持新手机的处理器架构。",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "新手机上的版本更高，不能降级安装。",
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "应用签名不同，请使用同一官方渠道的安装包。",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "新手机存储空间不足。",
        "INSTALL_FAILED_USER_RESTRICTED": "手机限制了安装，请在手机上允许此次安装。",
        "INSTALL_FAILED_MISSING_SPLIT": "应用的拆分安装文件不完整，请重新导出。",
        "INSTALL_FAILED_ABORTED": "手机中止了安装；若原始错误为 User rejected permissions，请在手机上允许安装。",
        "unauthorized": "请解锁手机，在弹窗中允许 USB 调试。",
        "device offline": "手机已离线，请检查数据线并重新连接。",
    }
    # 仅识别明确的设备缺失错误，不能把 APK 文件不存在误报成手机断线。
    if re.search(r"device ['\"].+?['\"] not found", message):
        explanation = "未找到目标设备，请检查手机连接并重新允许 USB 调试。"
        return message if message.startswith(explanation) else explanation + "\n" + message
    for code, explanation in reasons.items():
        if code in message:
            return message if message.startswith(explanation) else explanation + "\n" + message
    return message


class Stopped(Exception):
    """用户要求停止后续任务。"""


@dataclasses.dataclass
class Device:
    serial: str
    model: str
    state: str


@dataclasses.dataclass
class App:
    package: str
    name: str = ""
    version: str = ""
    version_code: str = ""
    paths: list[str] = dataclasses.field(default_factory=list)
    size: int = 0
    icon: bytes = b""
    files: list[str] = dataclasses.field(default_factory=list)
    hashes: dict[str, str] = dataclasses.field(default_factory=dict)
    status: str = "ready"
    error: str = ""
    session: str = ""
    operation: str = "export"
    source: str = ""

    @property
    def key(self) -> str:
        """同一应用的不同备份使用不同键，避免选择状态串到其他版本。"""
        return "|".join(self.files) or self.package


def check_stop(stop: threading.Event | None) -> None:
    """仅在安全边界停止任务，不在安装事务中强杀手机端进程。"""
    if stop is not None and stop.is_set():
        raise Stopped("已停止后续任务")


def find_adb() -> str:
    """优先使用用户设置和应用内置 ADB，再查找常见安装位置。"""
    configured = os.environ.get("HOARDY_ADB")
    candidates = [configured] if configured else []
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidates.extend([
        str(bundle / "platform-tools" / "adb"),
        shutil.which("adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("未找到 ADB，请在设置中选择 Android platform-tools 内的 adb 文件。")


class Adb:
    """使用参数数组调用 ADB，并为每个手机操作绑定明确的序列号。"""

    def __init__(self, serial: str | None = None, executable: str | None = None):
        """允许命令行保留自动选机行为；GUI 必须传入所选手机序列号。"""
        self.serial = serial
        self.executable = executable or find_adb()

    def run(self, args: list[str], timeout: int = 60) -> str:
        """执行命令并把超时、退出码和手机错误转成可展示的异常。"""
        command = [self.executable]
        if self.serial:
            command.extend(["-s", self.serial])
        try:
            result = subprocess.run(
                command + args, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace", check=False,
            )
        except subprocess.TimeoutExpired as exc:
            LOG.warning("ADB timeout serial=%s command=%s timeout=%s", self.serial, args[:1], timeout)
            raise RuntimeError("手机响应超时，请检查连接后重试。") from exc
        message = (result.stderr + "\n" + result.stdout).strip()
        if result.returncode or (args and args[0] in ("install", "install-multiple")
                                 and "Success" not in result.stdout):
            LOG.warning("ADB failed serial=%s command=%s exit=%s error=%s", self.serial, args[:1], result.returncode, message[:4000])
            raise RuntimeError(explain_error(message) if message else "手机操作失败")
        return result.stdout

    def shell(self, args: list[str]) -> str:
        """在手机 shell 中逐参数引用，兼容路径中的空格并避免命令注入。"""
        return self.run(["shell", shlex.join(args)])

    def read_range(self, path: str, offset: int, length: int) -> bytes:
        """按 64 KB 块读取 ZIP 资源，扫描应用时不搬运整个大型安装包。"""
        block = 65536
        skip, start = divmod(offset, block)
        count = (start + length + block - 1) // block
        command = [self.executable]
        if self.serial:
            command.extend(["-s", self.serial])
        # exec-out 直接把 sh -c 的参数交给手机；不能再给整条命令套一次引号。
        # 此通道可能合并标准错误，必须屏蔽 dd 的统计文本，避免污染 ZIP 二进制数据。
        remote = shlex.join(["dd", f"if={path}", f"bs={block}", f"skip={skip}", f"count={count}"])
        remote += " 2>/dev/null"
        try:
            result = subprocess.run(command + ["exec-out", "sh", "-c", remote],
                                    capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("读取应用资料超时，请检查手机连接。") from exc
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        data = result.stdout[start:start + length]
        if len(data) != length:
            raise RuntimeError("手机未返回完整的安装包资料，请检查连接或重新扫描。")
        return data

    def devices(self) -> list[Device]:
        """读取设备状态，未授权设备也返回以便显示授权引导。"""
        devices = []
        for line in self.run(["devices", "-l"], timeout=10).splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[0].startswith("*"):
                continue
            model = next((p[6:] for p in parts[2:] if p.startswith("model:")), parts[0])
            devices.append(Device(parts[0], model.replace("_", " "), parts[1]))
        return devices

    def packages(self, include_system: bool = False) -> set[str]:
        """只枚举主用户，首版不混入工作资料或应用分身的数据。"""
        args = ["pm", "list", "packages", "--user", "0"]
        if not include_system:
            args.append("-3")
        return {line[8:] for line in self.shell(args).splitlines()
                if line.startswith("package:") and PACKAGE.fullmatch(line[8:])}

    def paths(self, package: str) -> list[str]:
        """解析全部 APK 路径，拆分应用必须成组保存和安装。"""
        if not PACKAGE.fullmatch(package):
            raise ValueError("无效应用包名")
        result = [line[8:].strip() for line in self.shell(
            ["pm", "path", "--user", "0", package]).splitlines()
            if line.startswith("package:")]
        if not result:
            raise RuntimeError("找不到应用安装包，应用可能已经卸载或更新。")
        return result

    def sizes(self, paths: list[str]) -> int:
        """统计全部拆分安装文件的实际字节数，不把应用私有数据算进去。"""
        values = self.shell(["stat", "-c", "%s", *paths]).splitlines()
        if len(values) != len(paths):
            raise RuntimeError("无法读取安装包大小")
        return sum(int(value.strip()) for value in values)

    def connected(self) -> bool:
        """失败后确认原设备是否仍连接，避免把后续任务转交给其他手机。"""
        try:
            return self.run(["get-state"]).strip() == "device"
        except RuntimeError:
            return False


def digest(path: Path) -> str:
    """流式计算校验值，避免大安装包一次占满内存。"""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=1)
def find_aapt2() -> str | None:
    """复用已有 Android 原生资源解析工具；缺少时仍可使用 Python 解析。"""
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidates = [os.environ.get("HOARDY_AAPT2"), str(bundle / "tools/aapt2/aapt2"), shutil.which("aapt2")]
    sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
    candidates.extend(str(path) for path in sorted(sdk.glob("build-tools/*/aapt2"), reverse=True))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def read_native_metadata(path: Path, app: App) -> bool:
    """用原生工具快速读名称和版本，优先中文；不支持时保留原有解析路径。"""
    executable = find_aapt2()
    if not executable:
        return False
    try:
        result = subprocess.run([executable, "dump", "badging", str(path)], capture_output=True,
                                encoding="utf-8", errors="replace", timeout=20, check=False)
        if result.returncode:
            return False
        values = {}
        icons = []
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            if key == "package":
                fields = dict(re.findall(r"(\w+)='([^']*)'", value))
                app.version = fields.get("versionName", "")
                app.version_code = fields.get("versionCode", "")
            elif key.startswith("application-label"):
                values[key] = value.strip().removeprefix("'").removesuffix("'")
            elif key.startswith("application-icon"):
                icons.append(value.strip().removeprefix("'").removesuffix("'"))
        for key in ("application-label-zh-CN", "application-label-zh-Hans", "application-label-zh", "application-label"):
            if values.get(key):
                app.name = values[key]
                break
        with zipfile.ZipFile(path) as archive:
            for name in reversed(icons):
                if name.lower().endswith((".png", ".webp", ".jpg")) and name in archive.namelist():
                    app.icon = archive.read(name)
                    break
        return bool(app.name and app.name != app.package and app.version)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile):
        return False


class RemoteApk(io.RawIOBase):
    """为 ZIP 解析器提供可定位的手机文件读取，只传输真正需要的资源。"""

    def __init__(self, adb: Adb, path: str):
        """记录单个安装文件的尺寸，全部偏移只在该文件范围内读取。"""
        super().__init__()
        self.adb, self.path = adb, path
        self.length = adb.sizes([path])
        self.position = 0
        self.buffer = b""
        self.buffer_start = 0

    def seekable(self) -> bool:
        """允许 zipfile 从文件尾部定位中央目录。"""
        return True

    def tell(self) -> int:
        """返回当前读取位置。"""
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        """实现 ZIP 所需的绝对、相对和尾部定位，拒绝负偏移。"""
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        if position < 0:
            raise ValueError("无效安装包偏移")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        """复用同一数据块，ZIP 连续读取头部与文件名时不再反复启动 ADB。"""
        size = min(self.length - self.position, size if size >= 0 else self.length)
        if size <= 0:
            return b""
        end = self.position + size
        if not (self.buffer_start <= self.position and end <= self.buffer_start + len(self.buffer)):
            start = self.position // 65536 * 65536
            count = min(self.length - start, max(65536, end - start))
            self.buffer = self.adb.read_range(self.path, start, count)
            self.buffer_start = start
        offset = self.position - self.buffer_start
        data = self.buffer[offset:offset + size]
        self.position += len(data)
        return data


def cache_metadata(adb: Adb, source: str, destination: Path) -> None:
    """只缓存清单、资源表和少量启动图标，实际导出仍读取原始完整 APK。"""
    # 多窗口同时扫描时也各写自己的临时文件，完成后再原子替换同一缓存。
    partial = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with RemoteApk(adb, source) as remote, zipfile.ZipFile(remote) as archive:
            selected = [name for name in ("AndroidManifest.xml", "resources.arsc") if name in archive.namelist()]
            icons = [item.filename for item in archive.infolist()
                     if item.file_size < 256 * 1024 and item.filename.lower().endswith((".png", ".webp", ".jpg"))
                     and any(word in item.filename.lower() for word in ("launcher", "icon", "logo"))]
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as output:
                for name in selected + icons[:12]:
                    output.writestr(name, archive.read(name))
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def read_metadata(path: Path, app: App) -> None:
    """优先原生解析资源；失败时回退 Python，仍不阻止按包名导出。"""
    if read_native_metadata(path, app):
        return
    from apkutils import APK

    try:
        with zipfile.ZipFile(path) as archive, APK.from_file(str(path)) as apk:
            # apkutils 4 的名称/图标解析只依赖子文件名；预填其文件清单缓存，
            # 避免为探测 MIME 解压所有 DEX 和游戏资源，旧版完整 APK 缓存同样受益。
            apk.children = [{"name": item.filename, "type": "", "time": "", "crc": f"{item.CRC:08X}"}
                            for item in archive.infolist()]
            app.version = str(apk.version_name or "")
            manifest = ET.fromstring(apk.get_manifest())
            app.version_code = manifest.get("{http://schemas.android.com/apk/res/android}versionCode", "")
            app.name = str(apk.app_name or app.package)
            icons = apk.get_app_icons() or []
            for icon in icons:
                name = icon if isinstance(icon, str) else icon.get("name", "")
                if name.lower().endswith((".png", ".webp", ".jpg")):
                    app.icon = archive.read(name)
                    break
    except Exception:
        # 各厂商资源表和自适应图标存在差异，统一保留通用图标作为降级。
        app.name = app.name or app.package


def scan_apps(adb: Adb, cache: Path, notify: Notify,
              stop: threading.Event | None = None, library: Path | None = None) -> list[App]:
    """先发布全部应用，再在同一后台任务核对备份、补充资料，避免阻塞首屏。"""
    cache.mkdir(parents=True, exist_ok=True)
    stop = stop if stop is not None else threading.Event()
    check_stop(stop)
    packages = sorted(adb.packages())
    bases = {}
    for line in adb.shell(["pm", "list", "packages", "-f", "-3", "--user", "0"]).splitlines():
        if line.startswith("package:"):
            path, separator, package = line[8:].rpartition("=")
            if separator and package in packages:
                bases[package] = path
    records = []
    identities = {}
    for package in packages:
        identity = hashlib.sha256(bases.get(package, package).encode()).hexdigest()[:20]
        identities[package] = identity
        app = App(package, name=package, source=adb.serial or "", status="loading")
        summary = cache / f"{identity}.info.json"
        if summary.exists():
            try:
                saved = json.loads(summary.read_text(encoding="utf-8"))
                if saved["package"] == package:
                    saved["icon"] = base64.b64decode(saved.get("icon", ""), validate=True)
                    app = App(**saved)
                    app.source = adb.serial or ""
            except (OSError, ValueError, KeyError, TypeError):
                pass
        records.append(app)
    # 首屏事件早于任何 APK 资源读取，保证用户马上看到完整、可勾选的清单。
    notify({"kind": "apps", "apps": list(records), "completed": 0, "total": len(records)})
    if library is not None:
        notify({"kind": "export_index", "index": build_export_index(library),
                "library": str(library.resolve()), "completed": 0, "total": len(records)})

    def read_one(original: App) -> App:
        """只修改自己的副本，界面线程收到结果后再替换对应行。"""
        app = dataclasses.replace(original)
        check_stop(stop)
        try:
            app.paths = adb.paths(app.package)
            app.size = adb.sizes(app.paths)
            check_stop(stop)
            cached = cache / f"{identities[app.package]}.metadata.zip"
            source = next((path for path in app.paths if path.endswith("/base.apk")), app.paths[0])
            try:
                if not cached.exists():
                    cache_metadata(adb, source, cached)
                read_metadata(cached, app)
            except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
                # 资料读取失败不能触发大型 APK 的全量传输，也不妨碍用户导出原包。
                app.error = "名称或图标暂未读取，可按包名选择并导出。"
            app.status = "ready"
            if app.name != app.package and app.version:
                saved = dataclasses.asdict(app)
                saved["icon"] = base64.b64encode(app.icon).decode("ascii")
                temporary = cache / f"{identities[app.package]}.{uuid.uuid4().hex}.info.partial"
                temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                temporary.replace(cache / f"{identities[app.package]}.info.json")
        except Stopped:
            return original
        except Exception as exc:
            app.status, app.error = "unavailable", str(exc)
        return app

    ready = sum(app.status != "loading" for app in records)
    indexes = {app.package: index for index, app in enumerate(records)}
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="apk-info")
    futures = [pool.submit(read_one, app) for app in records if app.status == "loading"]
    try:
        for future in as_completed(futures):
            if stop.is_set():
                break
            app = future.result()
            records[indexes[app.package]] = app
            ready += 1
            notify({"kind": "app", "app": app, "completed": ready, "total": len(records)})
            if app.status == "unavailable" and not adb.connected():
                stop.set()
                raise RuntimeError("旧手机连接已中断，请重连后重新扫描。")
    finally:
        # 停止后不再启动排队任务；当前三个资源读取完成后即可安全转入导出。
        pool.shutdown(wait=True, cancel_futures=True)
    return records


def export_one(adb: Adb, app: App, prefix: Path,
               stop: threading.Event | None = None) -> None:
    """完整落盘后读取实际版本，不能把扫描到的旧版本记到新导出的 APK 上。"""
    app.files, app.hashes = [], {}
    paths = adb.paths(app.package) if app.source else app.paths or adb.paths(app.package)
    app.paths = paths
    for index, source in enumerate(paths):
        check_stop(stop)
        if len(paths) == 1:
            destination = Path(f"{prefix}__{app.package}.apk")
        else:
            destination = Path(f"{prefix}__{app.package}") / f"{index}_{Path(source).name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            partial = destination.with_suffix(destination.suffix + ".partial")
            try:
                adb.run(["pull", "-a", source, str(partial)], timeout=3600)
                with zipfile.ZipFile(partial) as archive:
                    if "AndroidManifest.xml" not in archive.namelist() or archive.testzip():
                        raise RuntimeError("安装包不完整，已保留为失败任务。")
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)
        app.files.append(str(destination.resolve()))
        app.hashes[destination.name] = digest(destination)
    if not app.files:
        raise RuntimeError("应用没有可导出的安装文件")
    app.size = sum(Path(path).stat().st_size for path in app.files)
    app.status, app.error = "exported", ""
    # 扫描后应用可能升级；使用新对象读取，失败时不会残留扫描快照的版本字段。
    actual = App(app.package, name=app.name or app.package)
    base = next((path for path in app.files if Path(path).name.endswith("base.apk")), app.files[0])
    try:
        read_metadata(Path(base), actual)
    except Exception:
        # 基础 CLI 可以没有可选解析依赖，文件导出成功与版本确认是独立结果。
        actual.version, actual.version_code = "", ""
    app.version, app.version_code = actual.version, actual.version_code
    if actual.name and actual.name != app.package:
        app.name = actual.name
    if actual.icon:
        app.icon = actual.icon
    if not (app.version or app.version_code):
        app.error = "版本未确认；安装包已成功导出，稍后可重新核对。"


def validate_files(app: App) -> None:
    """安装前验证全部文件及清单校验值，损坏备份不能进入安装事务。"""
    if not app.files:
        raise RuntimeError("备份中没有安装文件")
    for name in app.files:
        path = Path(name)
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise RuntimeError(f"安装文件不存在：{path.name}")
        expected = app.hashes.get(path.name)
        if expected and digest(path) != expected:
            raise RuntimeError(f"安装包校验失败：{path.name}")
        with zipfile.ZipFile(path) as archive:
            if "AndroidManifest.xml" not in archive.namelist() or archive.testzip():
                raise RuntimeError(f"安装包损坏：{path.name}")


def install_one(adb: Adb, app: App, installed: set[str], force: bool = False) -> None:
    """整组安装拆分文件；GUI 默认跳过已安装应用，不卸载也不覆盖。"""
    if app.package in installed and not force:
        app.status, app.error = "skipped", "新手机已安装，已跳过"
        return
    validate_files(app)
    command = "install-multiple" if len(app.files) > 1 else "install"
    adb.run([command, *(["-r"] if force else []), *app.files], timeout=3600)
    installed.add(app.package)
    app.status, app.error = "installed", ""


def save_session(folder: Path, apps: list[App], source: str, operation: str) -> None:
    """原子更新运行清单，保证退出或中断后仍能区分完成项和失败项。"""
    rows = []
    for app in apps:
        row = dataclasses.asdict(app)
        row["icon"] = base64.b64encode(app.icon).decode("ascii")
        row["files"] = [os.path.relpath(Path(path).resolve(), folder.resolve()) for path in app.files]
        row["session"] = ""
        rows.append(row)
    document = {"schema_version": 1, "created_at": folder.name, "serial": source,
                "operation": operation, "apps": rows}
    temporary = folder / "manifest.json.partial"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "manifest.json")


def run_batch(adb: Adb, apps: list[App], root: Path, operation: str,
              notify: Notify, stop: threading.Event, resume: Path | None = None) -> list[App]:
    """单项失败继续；断线释放工作线程，恢复时在同一清单只处理所选未完成项。"""
    if not adb.serial:
        raise RuntimeError("请明确选择手机后再开始")
    if resume is not None:
        folder = resume.resolve()
        if folder.parent != root.resolve() or operation != "install":
            raise RuntimeError("只能恢复当前应用库中的安装任务")
        doc = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if doc.get("serial") != adb.serial or doc.get("operation") != "install":
            raise RuntimeError("请重新连接本批次原来的新手机，不能把队列转交给其他设备。")
        records = [app for app in load_library(root, True) if Path(app.session).resolve() == folder]
        if not records:
            raise RuntimeError("原安装清单无法读取，已保留原文件，请检查应用库。")
        requested = {app.package for app in apps}
        work = [app for app in records if app.package in requested and app.status in ("failed", "pending")]
        if not work:
            return records
        for app in work:
            app.status, app.error = "pending", ""
    else:
        folder = root / (datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                         + "_" + operation + "_" + uuid.uuid4().hex[:6])
        folder.mkdir(parents=True)
        records = [dataclasses.replace(app, files=list(app.files), hashes=dict(app.hashes),
                                       status="pending", error="", operation=operation, session=str(folder))
                   for app in apps]
        work = list(records)
    save_session(folder, records, adb.serial, operation)
    LOG.info("Batch started operation=%s target=%s session=%s selected=%d total=%d resume=%s", operation, adb.serial,
             folder, len(work), len(records), resume is not None)
    notify({"kind": "batch", "apps": [dataclasses.replace(app) for app in records],
            "serial": adb.serial, "session": str(folder)})
    try:
        installed = adb.packages(True) if operation == "install" else set()
    except Exception as exc:
        # 初始查询也可能恰好遇到断线；不把尚未安装的应用逐个记成失败。
        LOG.warning("Batch paused during device query session=%s error=%s", folder, exc)
        notify({"kind": "paused", "reason": str(exc)})
        return records
    for app in work:
        if stop.is_set():
            break
        completed = sum(item.status != "pending" for item in records)
        notify({"kind": "progress", "app": app, "completed": completed, "total": len(records)})
        try:
            if operation == "export":
                export_one(adb, app, folder / "backup", stop)
            else:
                install_one(adb, app, installed)
        except Stopped:
            app.status, app.error = "pending", "已停止后续任务"
            break
        except Exception as exc:
            app.status, app.error = "failed", str(exc)
        finally:
            save_session(folder, records, adb.serial, operation)
            LOG.info("Batch result operation=%s package=%s status=%s error=%s", operation, app.package, app.status, app.error[:4000])
            completed = sum(item.status != "pending" for item in records)
            notify({"kind": "result", "app": app, "completed": completed, "total": len(records)})
        if app.status == "failed" and not adb.connected():
            LOG.warning("Batch paused after disconnect target=%s session=%s", adb.serial, folder)
            notify({"kind": "paused", "reason": "目标手机连接已断开，请重新连接原设备后继续剩余应用。"})
            break
    return records


def load_library(root: Path, history: bool = False) -> list[App]:
    """读取版本化清单和旧版备份；清单中的路径必须留在所选应用库内。"""
    if not root.exists():
        return []
    result, known = [], set()
    for manifest in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            if doc.get("schema_version") != 1:
                continue
            for data in doc["apps"]:
                data = dict(data)
                files = [(manifest.parent / p).resolve() for p in data.get("files", [])]
                if any(not file.is_relative_to(root.resolve()) for file in files):
                    continue
                data["files"] = [str(file) for file in files]
                data["icon"] = base64.b64decode(data.get("icon", ""), validate=True)
                known.update(data["files"])
                data["session"], data["operation"] = str(manifest.parent), doc["operation"]
                allowed = {field.name for field in dataclasses.fields(App)}
                app = App(**{key: value for key, value in data.items() if key in allowed})
                if history or (doc["operation"] == "export" and app.status == "exported"):
                    if not history and (not app.files or any(not Path(file).is_file() for file in app.files)
                                        or sum(Path(file).stat().st_size for file in app.files) != app.size):
                        app.status, app.error = "unavailable", "备份文件缺失或大小已改变，请重新导出。"
                    result.append(app)
        except (ValueError, KeyError, TypeError, OSError):
            continue
    if history:
        return result
    # 兼容上游原有单文件与拆分目录；不把新清单中的半成品重复识别为旧备份。
    for path in sorted(root.glob("*")):
        if path.name.startswith(".") or "__" not in path.name:
            continue
        files = [path] if path.suffix == ".apk" else sorted(path.glob("*.apk")) if path.is_dir() else []
        if not files or any(str(file.resolve()) in known for file in files):
            continue
        if any(not file.resolve().is_relative_to(root.resolve()) for file in files):
            continue
        package = path.name.split("__", 1)[1].removesuffix(".apk")
        if not PACKAGE.fullmatch(package):
            continue
        app = App(package, name=package, files=[str(file.resolve()) for file in files],
                  size=sum(file.stat().st_size for file in files), status="exported")
        read_metadata(files[0], app)
        result.append(app)
    return result


def compare_versions(current: App, backup: App) -> bool | None:
    """优先核对版本号；两个版本号不同时不能用相同版本名称掩盖差异。"""
    if current.version_code and backup.version_code:
        # XML 解析和原生工具可能分别返回十六进制与十进制文本，按整数语义比对。
        left, right = str(current.version_code).strip(), str(backup.version_code).strip()
        try:
            return int(left, 16 if left.lower().startswith("0x") else 10) == int(right, 16 if right.lower().startswith("0x") else 10)
        except ValueError:
            return None
    if current.version and backup.version:
        return current.version == backup.version
    return None


def build_export_index(root: Path) -> dict[str, list[App]]:
    """复用已有备份读取和轻量文件检查，保留每个包的所有可用成功副本。"""
    index: dict[str, list[App]] = {}
    for backup in load_library(root):
        if backup.status != "exported":
            continue
        if not backup.version_code:
            base = next((path for path in backup.files if Path(path).name.endswith("base.apk")), backup.files[0])
            try:
                read_metadata(Path(base), backup)
            except Exception:
                # 不能确认的旧备份仍参与“核对中”判断，而不是被误当作未导出。
                pass
        index.setdefault(backup.package, []).append(backup)
    return index


def export_state(current: App, index: dict[str, list[App]], ready: bool = True) -> str:
    """任一同版本有效副本即可确认已导出，未知版本单独返回核对中。"""
    backups = index.get(current.package, [])
    results = [compare_versions(current, backup) for backup in backups]
    if True in results:
        return "exported"
    if not ready or None in results:
        return "checking"
    return "not_exported"
