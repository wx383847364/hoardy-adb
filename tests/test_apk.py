"""用模拟设备验证迁移边界，不对真实手机执行安装或删除。"""

import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
import zipfile

from hoardy_adb import apk


def make_apk(path: Path, payload: bytes = b"demo") -> None:
    """生成仅供完整性测试的最小 ZIP，不把它当作可安装的真实应用。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"<manifest />")
        archive.writestr("classes.dex", payload)


class FakeAdb(apk.Adb):
    """模拟单 APK、拆分 APK、安装失败以及断线。"""

    def __init__(self, root: Path, serial: str = "OLD"):
        """所有远端文件映射到临时目录，测试不会触碰真机。"""
        super().__init__(serial, "fake-adb")
        self.root = root
        self.calls = []
        self.existing = set()
        self.online = True
        self.fail_pull = False
        self.fail_install = False

    def run(self, args: list[str], timeout: int = 60) -> str:
        """记录命令并生成下载文件，用失败开关验证错误恢复。"""
        self.calls.append(args)
        if args[0] == "pull":
            if self.fail_pull:
                Path(args[-1]).write_bytes(b"interrupted")
                raise RuntimeError("模拟传输失败")
            make_apk(Path(args[-1]), args[-2].encode())
        elif args[0] in ("install", "install-multiple"):
            if self.fail_install:
                raise RuntimeError("INSTALL_FAILED_NO_MATCHING_ABIS")
            return "Success\n"
        return ""

    def packages(self, include_system: bool = False) -> set[str]:
        """返回当前目标手机已经安装的应用。"""
        return set(self.existing)

    def paths(self, package: str) -> list[str]:
        """为指定应用提供完整拆分安装路径。"""
        return [f"/data/app/{package}/base.apk", f"/data/app/{package}/split_config.apk"]

    def connected(self) -> bool:
        """让测试精确控制断线是否终止后续队列。"""
        return self.online


class ApkTests(unittest.TestCase):
    """测试真正影响迁移正确性和已有备份安全的行为。"""

    def test_usb_diagnostic_distinguishes_missing_phone_and_missing_adb_interface(self) -> None:
        """USB 设备存在不等于调试可用，界面必须分别解释这两类断连。"""
        device = {"USB Serial Number": "NEW", "UsbDeviceSignature": b"MTP only"}
        with patch.object(apk.sys, "platform", "darwin"), patch.object(apk.subprocess, "run") as run:
            run.return_value = SimpleNamespace(stdout=plistlib.dumps([device]))
            self.assertIn("未提供 ADB 调试接口", apk.usb_diagnostic("NEW"))
            device["UsbDeviceSignature"] = b"MTP\xff\x42\x01"
            run.return_value = SimpleNamespace(stdout=plistlib.dumps([device]))
            self.assertIn("调试接口仍在", apk.usb_diagnostic("NEW"))
            run.return_value = SimpleNamespace(stdout=plistlib.dumps([]))
            self.assertIn("未检测到", apk.usb_diagnostic("NEW"))

    def test_local_logging_is_bounded_and_initialization_is_idempotent(self) -> None:
        """日志重复初始化不增加处理器，滚动文件保留错误内容并限制大小与数量。"""
        previous_level, previous_propagate = apk.LOG.level, apk.LOG.propagate
        with tempfile.TemporaryDirectory() as folder, patch.object(apk.LOG, "handlers", []):
            try:
                path = Path(folder) / "logs" / "app.log"
                apk.configure_logging(path)
                apk.configure_logging(path)
                self.assertEqual(len(apk.LOG.handlers), 1)
                handler = apk.LOG.handlers[0]
                self.assertEqual(handler.maxBytes, 2 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 3)
                apk.LOG.error("diagnostic test error")
                handler.flush()
                self.assertIn("diagnostic test error", path.read_text())
                handler.doRollover()
                apk.LOG.info("after rotation")
                handler.flush()
                self.assertTrue(Path(str(path) + ".1").exists())
                self.assertIn("after rotation", path.read_text())
            finally:
                for handler in apk.LOG.handlers:
                    handler.close()
                apk.LOG.setLevel(previous_level)
                apk.LOG.propagate = previous_propagate

    def test_install_error_explanations_preserve_original_and_file_errors(self) -> None:
        """中文原因不重复添加，设备缺失与本地文件不存在必须区分。"""
        rejected = "Failure [INSTALL_FAILED_ABORTED: User rejected permissions]"
        explained = apk.explain_error(rejected)
        self.assertIn(rejected, explained)
        self.assertIn("手机中止", explained)
        self.assertEqual(apk.explain_error(explained), explained)
        missing = "adb: device 'NEW' not found"
        self.assertIn("未找到目标设备", apk.explain_error(missing))
        self.assertEqual(apk.explain_error("APK file not found"), "APK file not found")

    def setUp(self) -> None:
        """每个测试使用独立临时目录。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adb = FakeAdb(self.root)
        # ZIP 传输用的是合成测试包，版本解析单独用明确的测试替身验证。
        metadata = patch("hoardy_adb.apk.read_metadata")
        self.metadata = metadata.start()
        self.addCleanup(metadata.stop)

    def collect(self, event: dict) -> None:
        """忽略界面通知，核心结果仍从清单和调用记录验证。"""
        pass

    def test_single_and_split_export_roundtrip(self) -> None:
        """单文件与拆分文件都应完整落盘并能从清单恢复。"""
        apps = [apk.App("com.test.single", paths=["/base.apk"]), apk.App("com.test.split")]
        result = apk.run_batch(self.adb, apps, self.root, "export", self.collect, threading.Event())
        self.assertEqual([a.status for a in result], ["exported", "exported"])
        restored = apk.load_library(self.root)
        self.assertEqual(sorted(len(a.files) for a in restored), [1, 2])
        self.assertTrue(all(a.hashes for a in restored))
        self.assertFalse(list(self.root.rglob("*.partial")))

    def test_subset_export_and_chinese_directory(self) -> None:
        """只传入所选应用时不能额外枚举并导出其他应用。"""
        destination = self.root / "中文 备份目录"
        apk.run_batch(self.adb, [apk.App("com.test.selected", paths=["/base.apk"])],
                      destination, "export", self.collect, threading.Event())
        self.assertEqual(len([call for call in self.adb.calls if call[0] == "pull"]), 1)
        self.assertEqual(len(apk.load_library(destination)), 1)

    def test_partial_export_is_not_installable(self) -> None:
        """中断产生的文件必须清理，失败项不能进入本地可安装清单。"""
        self.adb.fail_pull = True
        result = apk.run_batch(self.adb, [apk.App("com.test.bad")], self.root,
                               "export", self.collect, threading.Event())
        self.assertEqual(result[0].status, "failed")
        self.assertEqual(apk.load_library(self.root), [])
        self.assertFalse(list(self.root.rglob("*.partial")))
        self.assertEqual(apk.load_library(self.root, True)[0].status, "failed")

    def test_deleted_file_makes_library_item_unavailable(self) -> None:
        """备份完成后丢失文件时，应用库不能继续允许勾选安装。"""
        result = apk.run_batch(self.adb, [apk.App("com.test.missing")], self.root,
                               "export", self.collect, threading.Event())
        Path(result[0].files[-1]).unlink()
        self.assertEqual(apk.load_library(self.root)[0].status, "unavailable")

    def test_retry_exports_a_complete_new_backup(self) -> None:
        """失败后重试建立新批次，成功文件组不会混入前一次半成品。"""
        self.adb.fail_pull = True
        failed = apk.run_batch(self.adb, [apk.App("com.test.retry")], self.root,
                               "export", self.collect, threading.Event())
        self.adb.fail_pull = False
        retried = apk.run_batch(self.adb, failed, self.root, "export", self.collect, threading.Event())
        self.assertEqual(retried[0].status, "exported")
        self.assertNotEqual(retried[0].session, failed[0].session)
        self.assertEqual(len(apk.load_library(self.root)), 1)

    def test_existing_cli_backup_is_not_overwritten(self) -> None:
        """命令行重复导出时保留已有备份，不覆盖用户文件。"""
        app = apk.App("com.test.existing", paths=["/base.apk"])
        prefix = self.root / "backup"
        apk.export_one(self.adb, app, prefix)
        before = Path(app.files[0]).read_bytes()
        self.adb.fail_pull = True
        apk.export_one(self.adb, app, prefix)
        self.assertEqual(Path(app.files[0]).read_bytes(), before)

    def test_cli_wrappers_keep_split_backup_names_and_install(self) -> None:
        """原有命令行入口继续使用原拆分目录命名和成组恢复逻辑。"""
        from hoardy_adb.__main__ import cmd_backup_apks, cmd_restore_apks

        self.adb.existing = {"com.test.cli"}
        handler = Mock()
        with patch("hoardy_adb.__main__.apk_tools.Adb", return_value=self.adb):
            cmd_backup_apks(SimpleNamespace(prefix=str(self.root / "original"), include_system=False), handler)
            folder = self.root / "original__com.test.cli"
            self.assertTrue((folder / "0_base.apk").exists())
            self.adb.existing.clear()
            cmd_restore_apks(SimpleNamespace(paths=[str(folder)], force=False), handler)
        installs = [call for call in self.adb.calls if call[0] == "install-multiple"]
        self.assertEqual(len(installs), 1)

    def test_corrupted_backup_rejected_before_install(self) -> None:
        """校验值不一致时绝对不能向手机发送安装命令。"""
        app = apk.App("com.test.hash", paths=["/base.apk"])
        apk.export_one(self.adb, app, self.root / "backup")
        Path(app.files[0]).write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            apk.install_one(self.adb, app, set())
        self.assertFalse(any(call[0].startswith("install") for call in self.adb.calls))

    def test_split_install_is_one_transaction(self) -> None:
        """同一应用的全部拆分文件必须使用一次成组安装。"""
        app = apk.App("com.test.split")
        apk.export_one(self.adb, app, self.root / "backup")
        apk.install_one(self.adb, app, set())
        calls = [call for call in self.adb.calls if call[0].startswith("install")]
        self.assertEqual(calls, [["install-multiple", *app.files]])

    def test_installed_app_skipped_without_overwrite(self) -> None:
        """已有应用不能触发安装、覆盖或卸载。"""
        app = apk.App("com.test.existing")
        apk.install_one(self.adb, app, {app.package})
        self.assertEqual(app.status, "skipped")
        self.assertEqual(self.adb.calls, [])

    def test_failure_continues_but_disconnect_stops(self) -> None:
        """单项安装失败继续下项；断线时剩余项必须保持待处理。"""
        a, b = apk.App("com.test.a"), apk.App("com.test.b")
        for app in (a, b):
            apk.export_one(self.adb, app, self.root / "backup")
        self.adb.fail_install = True
        result = apk.run_batch(self.adb, [a, b], self.root, "install", self.collect, threading.Event())
        self.assertEqual([app.status for app in result], ["failed", "failed"])
        self.adb.online = False
        result = apk.run_batch(self.adb, [a, b], self.root, "install", self.collect, threading.Event())
        self.assertEqual([app.status for app in result], ["failed", "pending"])

    def test_stop_preserves_pending_items(self) -> None:
        """用户停止后，尚未执行的应用保留记录且不发送传输命令。"""
        stop = threading.Event()
        stop.set()
        result = apk.run_batch(self.adb, [apk.App("com.test.a")], self.root,
                               "export", self.collect, stop)
        self.assertEqual(result[0].status, "pending")
        self.assertEqual(self.adb.calls, [])

    def test_resume_keeps_success_failure_and_processes_only_pending(self) -> None:
        """恢复沿用同一清单，仅处理所选未处理项，旧成功与失败统计不得丢失。"""
        apps = [apk.App("com.test." + suffix) for suffix in ("ok", "fail", "later")]
        for app in apps:
            apk.export_one(self.adb, app, self.root / "backup")
        session = self.root / "2026-01-01_install"
        session.mkdir()
        apps[0].status, apps[1].status, apps[2].status = "installed", "failed", "pending"
        apps[1].error = "original failure"
        apk.save_session(session, apps, self.adb.serial, "install")
        self.adb.calls.clear()
        result = apk.run_batch(self.adb, [apps[2]], self.root, "install", self.collect, threading.Event(), session)
        self.assertEqual([app.status for app in result], ["installed", "failed", "installed"])
        self.assertEqual(result[1].error, "original failure")
        self.assertEqual(len([call for call in self.adb.calls if call[0].startswith("install")]), 1)
        self.assertEqual(len(list(self.root.glob("*/manifest.json"))), 1)
        self.adb.calls.clear()
        retried = apk.run_batch(self.adb, [result[1]], self.root, "install", self.collect, threading.Event(), session)
        self.assertTrue(all(app.status == "installed" for app in retried))
        self.assertEqual(len([call for call in self.adb.calls if call[0].startswith("install")]), 1)

    def test_resume_rejects_different_phone_before_writing(self) -> None:
        """不同目标设备不得接管旧队列，拒绝操作不能修改清单。"""
        session = self.root / "2026-01-01_install"
        session.mkdir()
        app = apk.App("com.test.pending", status="pending", operation="install")
        apk.save_session(session, [app], "ORIGINAL", "install")
        before = (session / "manifest.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "原来的新手机"):
            apk.run_batch(self.adb, [app], self.root, "install", self.collect, threading.Event(), session)
        self.assertEqual(before, (session / "manifest.json").read_bytes())

    def test_retry_failed_does_not_attempt_pending_items(self) -> None:
        """用户只勾选一个失败项时，重试不得连带处理其余待执行应用。"""
        apps = [apk.App("com.test.failed"), apk.App("com.test.pending")]
        for app in apps:
            apk.export_one(self.adb, app, self.root / "backup")
        apps[0].status, apps[1].status = "failed", "pending"
        session = self.root / "2026-01-01_install"
        session.mkdir()
        apk.save_session(session, apps, self.adb.serial, "install")
        self.adb.calls.clear()
        result = apk.run_batch(self.adb, [apps[0]], self.root, "install", self.collect, threading.Event(), session)
        self.assertEqual([app.status for app in result], ["installed", "pending"])
        self.assertEqual(len([call for call in self.adb.calls if call[0].startswith("install")]), 1)

    def test_initial_disconnect_preserves_whole_queue(self) -> None:
        """起始设备查询断线也应发布可恢复队列，不把未尝试应用记成失败。"""
        events = []
        with patch.object(self.adb, "packages", side_effect=RuntimeError("device offline")):
            result = apk.run_batch(self.adb, [apk.App("com.test.pending")], self.root,
                                   "install", events.append, threading.Event())
        self.assertEqual(result[0].status, "pending")
        self.assertEqual([event["kind"] for event in events], ["batch", "paused"])

    @patch("hoardy_adb.apk.read_metadata")
    def test_legacy_backups_and_directory_metadata(self, metadata) -> None:
        """旧备份目录中的非 APK 文件不得被误送进安装事务。"""
        make_apk(self.root / "backup__com.test.single.apk")
        folder = self.root / "backup__com.test.split"
        folder.mkdir()
        make_apk(folder / "base.apk")
        make_apk(folder / "split.apk")
        (folder / "note.txt").write_text("not an APK")
        apps = apk.load_library(self.root)
        self.assertEqual(sorted(len(app.files) for app in apps), [1, 2])

    def test_manifest_path_cannot_escape_library(self) -> None:
        """恶意或损坏清单不能诱导工具读取应用库以外的文件。"""
        folder = self.root / "session"
        folder.mkdir()
        document = {"schema_version": 1, "operation": "export", "apps": [{
            "package": "com.test.escape", "status": "exported", "files": ["../../secret.apk"]}]}
        (folder / "manifest.json").write_text(json.dumps(document))
        self.assertEqual(apk.load_library(self.root), [])

    def test_adb_serial_and_quoting(self) -> None:
        """每个手机命令必须显式带设备号，带空格路径必须保持单个参数。"""
        result = subprocess.CompletedProcess([], 0, stdout="package:com.demo\n", stderr="")
        with patch("subprocess.run", return_value=result) as run:
            adb = apk.Adb("SERIAL-NEW", "adb")
            adb.shell(["stat", "-c", "%s", "/data/app/space name/base.apk"])
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["adb", "-s", "SERIAL-NEW"])
        self.assertIn("'/data/app/space name/base.apk'", command[-1])

    def test_exec_out_range_does_not_quote_entire_command(self) -> None:
        """红魔 exec-out 不需要额外命令引号，dd 统计输出不能进入二进制结果。"""
        result = subprocess.CompletedProcess([], 0, stdout=b"PK\x03\x04" + b"x" * 65532, stderr=b"")
        with patch("subprocess.run", return_value=result) as run:
            data = apk.Adb("REDMAGIC", "adb").read_range("/data/app/space name/base.apk", 0, 4)
        command = run.call_args.args[0]
        self.assertEqual(command[3:6], ["exec-out", "sh", "-c"])
        self.assertTrue(command[-1].startswith("dd "))
        self.assertTrue(command[-1].endswith(" 2>/dev/null"))
        self.assertIn("'if=/data/app/space name/base.apk'", command[-1])
        self.assertEqual(data, b"PK\x03\x04")

    def test_exec_out_short_binary_read_is_rejected(self) -> None:
        """即使 adb 返回成功，短数据也不能作为有效 ZIP 资料继续解析。"""
        result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch("subprocess.run", return_value=result), self.assertRaisesRegex(RuntimeError, "完整"):
            apk.Adb("REDMAGIC", "adb").read_range("/base.apk", 0, 16)

    def test_scan_publishes_all_packages_before_metadata(self) -> None:
        """完整清单必须先出现，用户停止资料读取也不能只看到前几个应用。"""
        self.adb.existing = {"com.test.first", "com.test.second"}
        stop = threading.Event()
        events = []

        def capture(event: dict) -> None:
            """记录首屏事件后立即停止，模拟用户不愿等待完整资料扫描。"""
            events.append(event)
            if event["kind"] == "apps":
                self.assertEqual(len(event["apps"]), 2)
                self.assertEqual(self.adb.calls, [])
                stop.set()

        with patch.object(self.adb, "shell", return_value="package:/base.apk=com.test.first\npackage:/two.apk=com.test.second\n"):
            result = apk.scan_apps(self.adb, self.root / "cache", capture, stop)
        self.assertEqual(len(result), 2)
        self.assertEqual(events[0]["kind"], "apps")
        self.assertEqual(self.adb.calls, [])

    def test_cached_scan_does_not_read_remote_apks(self) -> None:
        """再次刷新直接复用已解析资料，不重新搬运安装包或解析大资源。"""
        package = "com.test.cached"
        self.adb.existing = {package}
        cache = self.root / "cache"
        cache.mkdir()
        identity = apk.hashlib.sha256(b"/base.apk").hexdigest()[:20]
        app = apk.App(package, name="缓存应用", version="1.0", paths=["/base.apk"], size=123)
        saved = apk.dataclasses.asdict(app)
        saved["icon"] = ""
        (cache / f"{identity}.info.json").write_text(json.dumps(saved))
        with patch.object(self.adb, "shell", return_value=f"package:/base.apk={package}\n"), patch.object(self.adb, "paths") as paths:
            result = apk.scan_apps(self.adb, cache, self.collect)
        paths.assert_not_called()
        self.assertEqual(result[0].name, "缓存应用")

    def test_native_metadata_prefers_chinese_name(self) -> None:
        """原生解析优先简体中文名称，并保持真实版本信息。"""
        path = self.root / "sample.apk"
        make_apk(path)
        text = "package: name='com.test.chat' versionCode='10' versionName='2.0'\napplication-label:'Chat'\napplication-label-zh-CN:'聊天工具'\n"
        result = subprocess.CompletedProcess([], 0, stdout=text, stderr="")
        app = apk.App("com.test.chat")
        with patch("hoardy_adb.apk.find_aapt2", return_value="/aapt2"), patch("subprocess.run", return_value=result):
            self.assertTrue(apk.read_native_metadata(path, app))
        self.assertEqual((app.name, app.version, app.version_code), ("聊天工具", "2.0", "10"))

    def test_native_tool_unavailable_can_fall_back(self) -> None:
        """原生工具缺失或架构不支持不能使应用资料读取整体失败。"""
        with patch("hoardy_adb.apk.find_aapt2", return_value="/aapt2"), patch("subprocess.run", side_effect=OSError("Bad CPU type")):
            self.assertFalse(apk.read_native_metadata(self.root / "sample.apk", apk.App("com.test.a")))

    def test_remote_zip_adjacent_reads_share_one_transfer(self) -> None:
        """ZIP 的多个相邻小读取应共用一次手机传输，避免每几十字节启动一次 ADB。"""
        source = b"0123456789" * 10000
        with patch.object(apk.Adb, "sizes", return_value=len(source)), patch.object(
            apk.Adb, "read_range", side_effect=RangeReader(source)) as reader:
            remote = apk.RemoteApk(apk.Adb("OLD", "adb"), "/base.apk")
            self.assertEqual(remote.read(30), source[:30])
            self.assertEqual(remote.read(20), source[30:50])
            remote.seek(100)
            self.assertEqual(remote.read(100), source[100:200])
            self.assertEqual(reader.call_count, 1)

    def actual_version(self, path: Path, app: apk.App) -> None:
        """模拟从落盘 APK 读取到新版本，不复用传入对象的扫描版本。"""
        app.version, app.version_code = "2.0", "20"

    def test_export_records_actual_apk_version_after_upgrade(self) -> None:
        """扫描后升级的 App 必须记录实际导出的版本，不能保留旧快照。"""
        self.metadata.side_effect = self.actual_version
        app = apk.App("com.test.upgraded", version="1.0", version_code="10", paths=["/base.apk"])
        apk.export_one(self.adb, app, self.root / "backup")
        self.assertEqual((app.version, app.version_code), ("2.0", "20"))
        self.assertEqual(app.status, "exported")

    def test_failed_version_parse_keeps_successful_files_but_clears_old_version(self) -> None:
        """版本解析失败仍保留成功文件，但不能继续使用扫描时版本标记已导出。"""
        def incomplete_version(path: Path, parsed: apk.App) -> None:
            """模拟解析器已写入部分字段后失败，不能把半成品当作确认版本。"""
            parsed.version, parsed.version_code = "2.0", "20"
            raise ValueError("无法解析版本")

        self.metadata.side_effect = incomplete_version
        app = apk.App("com.test.unknown", version="1.0", version_code="10", paths=["/base.apk"])
        apk.export_one(self.adb, app, self.root / "backup")
        self.assertEqual((app.version, app.version_code), ("", ""))
        self.assertEqual(app.status, "exported")
        self.assertIn("版本未确认", app.error)
        self.assertTrue(Path(app.files[0]).is_file())

    def test_different_codes_do_not_match_equal_version_names(self) -> None:
        """两个明确版本号不同时，不能再用相同版本名称判定同版。"""
        current = apk.App("com.test.a", version="2.0", version_code="20")
        backup = apk.App("com.test.a", version="2.0", version_code="19")
        self.assertFalse(apk.compare_versions(current, backup))
        backup.version_code = ""
        self.assertTrue(apk.compare_versions(current, backup))
        backup.version = ""
        self.assertIsNone(apk.compare_versions(current, backup))
        current.version_code, backup.version_code = "0x14", "00020"
        self.assertTrue(apk.compare_versions(current, backup))

    def test_valid_old_copy_survives_missing_new_copy_and_failed_retry(self) -> None:
        """同版本只需一份有效副本，较新的缺失文件和失败记录不能抹掉旧成功。"""
        self.metadata.side_effect = self.actual_version
        item = apk.App("com.test.copies")
        old = apk.run_batch(self.adb, [item], self.root, "export", self.collect, threading.Event())[0]
        newer = apk.run_batch(self.adb, [item], self.root, "export", self.collect, threading.Event())[0]
        Path(newer.files[0]).unlink()
        self.adb.fail_pull = True
        apk.run_batch(self.adb, [item], self.root, "export", self.collect, threading.Event())
        with patch("hoardy_adb.apk.digest", side_effect=AssertionError("索引不能全量计算校验值")):
            index = apk.build_export_index(self.root)
        current = apk.App(item.package, version="2.0", version_code="20")
        self.assertEqual(apk.export_state(current, index), "exported")
        self.assertEqual(index[item.package][0].files, old.files)
        current.version_code = "21"
        self.assertEqual(apk.export_state(current, index), "not_exported")

    def test_old_record_missing_version_is_resolved_from_local_apk(self) -> None:
        """旧清单无版本时只读取本地基础 APK，重建索引后能够正确识别。"""
        apk.run_batch(self.adb, [apk.App("com.test.old")], self.root, "export", self.collect, threading.Event())
        calls = len(self.adb.calls)
        self.metadata.side_effect = self.actual_version
        index = apk.build_export_index(self.root)
        self.assertEqual(apk.export_state(apk.App("com.test.old", version_code="20"), index), "exported")
        self.assertEqual(len(self.adb.calls), calls)

    def test_legacy_name_only_record_is_checked_for_real_version_code(self) -> None:
        """旧清单仅有版本名称也先尝试读取实际版本号，避免同名不同构建被误判。"""
        result = apk.run_batch(self.adb, [apk.App("com.test.nameonly")], self.root, "export", self.collect, threading.Event())
        result[0].version, result[0].version_code = "2.0", ""
        apk.save_session(Path(result[0].session), result, "OLD", "export")
        self.metadata.side_effect = self.actual_version
        index = apk.build_export_index(self.root)
        self.assertEqual(apk.export_state(apk.App("com.test.nameonly", version="2.0", version_code="19"), index), "not_exported")

    def test_unknown_version_stays_checking(self) -> None:
        """缺少比较依据与索引未就绪单独标为核对中，不误标已导出或未导出。"""
        current = apk.App("com.test.a", version_code="20")
        index = {current.package: [apk.App(current.package)]}
        self.assertEqual(apk.export_state(current, index), "checking")
        self.assertEqual(apk.export_state(current, {}, False), "checking")
        self.assertEqual(apk.export_state(current, {}), "not_exported")

    def test_scan_emits_complete_list_before_export_index(self) -> None:
        """备份核对接在首屏清单后面，不为读取导出索引延迟手机清单。"""
        self.adb.existing = {"com.test.first"}
        stop = threading.Event()
        kinds = []

        def capture(event: dict) -> None:
            """记录顺序并结束资料扫描，只验证清单和索引的先后关系。"""
            kinds.append(event["kind"])
            if event["kind"] == "export_index":
                stop.set()

        with patch.object(self.adb, "shell", return_value="package:/base.apk=com.test.first\n"):
            apk.scan_apps(self.adb, self.root / "cache", capture, stop, library=self.root / "library")
        self.assertEqual(kinds[:2], ["apps", "export_index"])

    def test_metadata_reads_only_required_zip_resources(self) -> None:
        """扫描只缓存资源，不复制大 DEX；远端随机读取保持 ZIP 边界正确。"""
        source = self.root / "source.apk"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("AndroidManifest.xml", "<manifest />")
            archive.writestr("resources.arsc", b"resources")
            archive.writestr("classes.dex", b"x" * 1024 * 1024)
        data = source.read_bytes()
        with patch.object(apk.Adb, "sizes", return_value=len(data)), patch.object(
            apk.Adb, "read_range", side_effect=RangeReader(data)):
            target = self.root / "metadata.zip"
            apk.cache_metadata(apk.Adb("OLD", "adb"), "/base.apk", target)
        with zipfile.ZipFile(target) as archive:
            self.assertNotIn("classes.dex", archive.namelist())
            self.assertEqual(archive.read("resources.arsc"), b"resources")


class RangeReader:
    """用内存数据模拟 ADB 随机读取，验证 ZIP 定位而不连接手机。"""

    def __init__(self, data: bytes):
        """保存源字节供随机读取。"""
        self.data = data

    def __call__(self, path: str, offset: int, length: int) -> bytes:
        """返回对应区间，与手机端范围读取约定一致。"""
        return self.data[offset:offset + length]


if __name__ == "__main__":
    unittest.main()
