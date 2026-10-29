"""验证搜索、排序、多选和设备角色，不依赖真实手机。"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from hoardy_adb import apk
from hoardy_adb.apk import App, Device
from unittest.mock import Mock
import threading
from dataclasses import replace
from hoardy_adb.gui import Window, ACTIVE_ROLE


class GuiTests(unittest.TestCase):
    """在离屏 Qt 环境中验证实际界面行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个测试进程只创建一个 Qt 应用实例。"""
        cls.application = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        """删除临时设置，避免污染真实工具的保存位置。"""
        cls.temp.cleanup()

    def setUp(self) -> None:
        """明确使用临时 INI 文件替代窗口设置，不能让测试写入 Mac 原生偏好。"""
        self.settings = QSettings(str(Path(self.temp.name) / (self._testMethodName + ".ini")), QSettings.Format.IniFormat)
        settings = patch("hoardy_adb.gui.QSettings", return_value=self.settings)
        settings.start()
        self.addCleanup(settings.stop)
        self.poll = patch.object(Window, "refresh_devices")
        self.poll.start()
        self.addCleanup(self.poll.stop)
        self.window = Window()
        self.window.timer.stop()
        self.addCleanup(self.window.close)
        self.apps = [App("com.test.chat", name="微信", version="1.0", version_code="10", size=2 * 1024**2),
                     App("com.test.video", name="视频", version="1.0", version_code="10", size=10 * 1024**2),
                     App("com.test.map", name="地图", version="1.0", version_code="10", size=5 * 1024**2),
                     App("com.test.bad", name="不可用", status="unavailable")]
        self.window.phone_records = list(self.apps)
        self.window.export_index_ready = True
        self.window.set_records(self.apps)

    def backup(self, app: App, code: str = "10") -> App:
        """构造已经由后台验证成功的结果，界面测试不读写真实 APK。"""
        return App(app.package, name=app.name, version="1.0", version_code=code, size=app.size,
                   files=[f"/test/backup__{app.package}.apk"], status="exported")

    def row_for(self, package: str) -> int:
        """按包名找行，测试不能依赖排序后的固定行号。"""
        return next(row for row in range(self.window.table.rowCount())
                    if self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == package)

    def visible_packages(self) -> set[str]:
        """返回当前实际可见行，以验证隐藏和排序组合。"""
        return {self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.window.table.rowCount()) if not self.window.table.isRowHidden(row)}

    def deliver_index(self, index: dict) -> None:
        """通过真实事件入口交付索引，覆盖异步核对路径。"""
        self.window.handle_event({"kind": "export_index", "index": index,
                                  "library": str(self.window.library.resolve()), "completed": 0, "total": 4})

    def test_select_all_filtered_keeps_hidden_selection(self) -> None:
        """筛选后的全选只处理可见行，切换筛选仍保留之前的选择。"""
        self.window.search.setText("微信")
        self.window.header_clicked(0)
        self.window.search.setText("video")
        self.window.header_clicked(0)
        self.window.search.clear()
        self.assertEqual(self.window.selected["phone"], {"com.test.chat", "com.test.video"})
        self.assertEqual(self.window.total.text(), "12.0 MB")

    def test_settings_use_explicit_temporary_file(self) -> None:
        """测试窗口必须使用临时 INI 文件，不能修改用户正在使用的 Mac 偏好设置。"""
        self.assertIs(self.window.settings, self.settings)
        self.assertEqual(self.settings.format(), QSettings.Format.IniFormat)
        self.assertTrue(Path(self.settings.fileName()).is_relative_to(Path(self.temp.name)))

    def test_unchanged_device_poll_does_not_rebuild_any_ui(self) -> None:
        """相同设备快照不清空下拉框、不重建表格，也不重绘所选应用摘要。"""
        devices = [Device("OLD", "红魔", "device"), Device("NEW", "荣耀", "device")]
        self.window.set_devices(devices)
        self.window.select_visible(True)
        summary_item = self.window.selected_list.item(0)
        with patch.object(self.window.source, "clear") as clear, patch.object(self.window, "set_records") as table, patch.object(self.window, "update_summary") as summary:
            self.window.set_devices(devices)
        clear.assert_not_called()
        table.assert_not_called()
        summary.assert_not_called()
        self.assertIs(self.window.selected_list.item(0), summary_item)

    def test_disconnect_and_detection_error_keep_rows_selection_and_device_role(self) -> None:
        """断线或检测报错只使操作暂不可用，不丢失列表、勾选与原设备角色。"""
        devices = [Device("OLD", "红魔", "device")]
        self.window.set_devices(devices)
        self.window.source_serial = "OLD"
        self.window.select_visible(True)
        selected = set(self.window.selected["phone"])
        row = self.window.table.item(0, 1)
        self.window.set_devices([])
        self.assertEqual(self.window.source.currentData(), "OLD")
        self.assertIn("未连接", self.window.source.currentText())
        self.assertEqual(self.window.serial(), "")
        self.assertEqual(self.window.phone_records, self.apps)
        self.assertEqual(self.window.selected["phone"], selected)
        self.assertIs(self.window.table.item(0, 1), row)
        self.assertFalse(self.window.action.isEnabled())
        self.window.set_devices(devices)
        self.assertTrue(self.window.action.isEnabled())
        self.window.device_poll_error("temporary ADB error")
        self.assertEqual(self.window.selected["phone"], selected)
        self.assertIs(self.window.table.item(0, 1), row)
        self.assertFalse(self.window.action.isEnabled())
        self.window.set_devices(devices)
        self.assertTrue(self.window.action.isEnabled())

    def test_usb_hint_updates_without_reloading_list(self) -> None:
        """设备区解释缺少调试接口时，不清空或重建用户当前应用列表。"""
        self.window.mode = "library"
        item = self.window.table.item(0, 1)
        hint = "USB 已连接，但手机未提供 ADB 调试接口。"
        with patch.object(self.window, "set_records") as rebuild:
            self.window.device_poll_event({"kind": "usb_diagnostic", "hint": hint})
        rebuild.assert_not_called()
        self.assertEqual(self.window.connection.text(), hint)
        self.assertEqual(self.window.action_reason.text(), hint)
        self.assertIs(self.window.table.item(0, 1), item)

    def test_list_refresh_changes_only_modified_rows_and_preserves_scroll(self) -> None:
        """同一页局部刷新仅替换变化行，保留未变控件、勾选、排序与滚动位置。"""
        apps = [App(f"com.test.item{i}", name=f"应用 {i}") for i in range(30)]
        self.window.set_records(apps)
        self.window.table.resize(700, 260)
        self.window.show()
        self.application.processEvents()
        self.window.select_visible(True)
        stable_row = self.row_for(apps[0].package)
        stable_item = self.window.table.item(stable_row, 1)
        self.window.table.verticalScrollBar().setValue(10)
        before = self.window.table.verticalScrollBar().value()
        apps[1].name = "后台更新后的名字"
        self.window.set_records(apps)
        self.assertIs(self.window.table.item(self.row_for(apps[0].package), 1), stable_item)
        self.assertIn(apps[1].name, self.window.table.item(self.row_for(apps[1].package), 1).text())
        self.assertEqual(self.window.table.verticalScrollBar().value(), before)
        self.assertEqual(len(self.window.selected["phone"]), 30)
        self.window.set_records(apps[:-1] + [App("com.test.added")])
        self.assertEqual(self.window.table.rowCount(), 30)
        self.assertNotIn(apps[-1].key, self.window.selected["phone"])

    def test_library_refresh_keeps_selection_summary_and_batch_options(self) -> None:
        """刷新当前应用库不清空选择、不重建批次下拉框或相同的所选清单。"""
        self.window.mode = "library"
        apps = [App("com.test.backup", files=["/backup/base.apk"], status="exported", session="/backup/batch")]
        self.window.set_library_records(apps)
        self.window.select_visible(True)
        summary_item = self.window.selected_list.item(0)
        with patch.object(self.window.batch_filter, "clear") as clear:
            self.window.set_library_records([replace(apps[0])])
        clear.assert_not_called()
        self.assertEqual(self.window.selected["library"], {apps[0].key})
        self.assertIs(self.window.selected_list.item(0), summary_item)

    def test_device_poll_has_independent_worker_and_does_not_lock_ui(self) -> None:
        """自动设备检测不抢占业务线程，不禁用设备与导航，不显示全局进度。"""
        self.poll.stop()
        before = self.window.nav.isEnabled(), self.window.source.isEnabled(), self.window.progress.isHidden()
        with patch("hoardy_adb.gui.Worker") as worker:
            self.window.refresh_devices()
            self.window.refresh_devices()
            worker.assert_called_once()
            self.assertIsNone(self.window.worker)
            self.assertEqual((self.window.nav.isEnabled(), self.window.source.isEnabled(), self.window.progress.isHidden()), before)
            self.window.device_poll_finished()
        self.assertIsNone(self.window.device_worker)

    def test_startup_restores_target_before_automatic_phone_scan(self) -> None:
        """唯一在线手机可能是新手机，必须先恢复原队列目标再决定是否扫描。"""
        devices = [Device("NEW", "荣耀", "device")]
        with patch.object(self.window, "refresh_export_index") as restore, patch.object(self.window, "scan_phone") as scan:
            self.window.device_poll_done(devices)
            restore.assert_called_once()
            scan.assert_not_called()
            self.window.handle_event({"kind": "batch_details", "library": str(self.window.library.resolve()),
                                      "serial": "NEW", "session": "/library/install", "apps": []})
            self.window.job_kind = "index"
            self.window.job_finished()
            scan.assert_not_called()
        self.assertEqual(self.window.serial(), "")
        self.assertEqual(self.window.serial(True), "NEW")

    def test_refresh_library_does_not_touch_device_widgets(self) -> None:
        """列表刷新仅调度当前列表读取，不调用设备检测或重置设备区。"""
        self.window.mode = "library"
        with patch.object(self.window, "load_view") as load, patch.object(self.window, "set_devices") as devices:
            self.window.refresh_all()
        load.assert_called_once()
        devices.assert_not_called()

    def test_failure_dialog_shows_failed_pending_and_full_errors(self) -> None:
        """详情窗口分别展示失败和未处理，切换行可复制对应原始错误而不丢失信息。"""
        failed = App("com.test.fail", name="失败应用", status="failed", operation="install",
                     error="Failure [INSTALL_FAILED_ABORTED: User rejected permissions]", session="/backup/install")
        pending = App("com.test.pending", name="未执行应用", status="pending", operation="install")
        success = App("com.test.ok", status="installed", operation="install")
        self.window.job_kind = "install"
        self.window.job_done([failed, pending, success])
        self.assertTrue(self.window.failure_button.isEnabled())
        self.assertIn("失败 1 · 待处理 1", self.window.failure_button.text())

        self.assertTrue(self.window.failure_panel.isHidden())
        self.window.failure_button.click()
        self.assertFalse(self.window.failure_panel.isHidden())
        self.assertEqual(self.window.failure_table.rowCount(), 1)
        self.assertIn("User rejected permissions", self.window.failure_error.toPlainText())
        self.assertTrue(self.window.failure_error.isReadOnly())
        self.window.select_all_failures(True)
        self.assertEqual(self.window.failure_selection, {failed.package})
        self.window.failure_filter.setCurrentIndex(1)
        self.assertEqual(self.window.failure_table.rowCount(), 1)
        self.assertIn("尚未执行", self.window.failure_error.toPlainText())
        self.assertIn(pending.package, self.window.failure_error.toPlainText())
        self.assertFalse(self.window.failure_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.window.failure_button.click()
        self.assertTrue(self.window.failure_panel.isHidden())

    def test_library_restores_install_details_and_retry_after_restart(self) -> None:
        """重启后的详情来自已有清单，重试仍使用安装操作，迟到的其他目录结果无效。"""
        with tempfile.TemporaryDirectory() as folder:
            self.window.library = Path(folder)
            session = Path(folder) / "2026-01-01_install"
            session.mkdir()
            failed = App("com.test.fail", status="failed", error="device 'NEW' not found", operation="install")
            pending = App("com.test.pending", status="pending", operation="install")
            apk.save_session(session, [failed, pending], "NEW", "install")
            self.window.library_job(False, self.window.handle_event, threading.Event())
            self.assertEqual(len(self.window.failure_records), 2)
            self.assertTrue(self.window.failure_button.isEnabled())
            with patch.object(self.window, "begin_batch") as start:
                self.window.retry_failed()
                self.window.select_all_failures(True)
                self.window.retry_selected_failures()
            self.assertEqual(start.call_args.args[1], "install")
            self.assertEqual(len(start.call_args.args[0]), 1)
            self.assertEqual(start.call_args.args[2], session)
            with patch.object(self.window, "begin_batch") as resume:
                self.window.continue_pending()
            self.assertEqual(resume.call_args.args[0][0].package, pending.package)
            self.assertEqual(resume.call_args.args[2], session)
            self.window.handle_event({"kind": "batch_details", "library": "/another-library", "apps": []})
            self.assertEqual(len(self.window.failure_records), 2)

    def test_install_result_enables_failure_details_before_batch_finishes(self) -> None:
        """单项失败一到达即可打开详情，不必等整个批次结束。"""
        self.window.job_kind = "install"
        failed = App("com.test.fail", status="failed", error="denied", operation="install")
        self.window.handle_event({"kind": "result", "app": failed, "completed": 1, "total": 3})
        self.assertTrue(self.window.failure_button.isEnabled())
        self.assertEqual(self.window.failure_records, [failed])

    def test_running_install_is_highlighted_and_clears_after_result(self) -> None:
        """安装事件显示当前项与排队项，成功返回后必须停止动画并清除运行标记。"""
        self.window.mode = "library"
        first = App("com.test.first", name="当前应用", files=["/backup/first.apk"], status="exported")
        second = App("com.test.second", files=["/backup/second.apk"], status="exported")
        self.window.set_library_records([first, second])
        self.window.job_kind = "install"
        pending = [replace(app, status="pending", operation="install") for app in (first, second)]
        self.window.failure_records = pending
        self.window.handle_event({"kind": "progress", "app": pending[0], "completed": 0, "total": 2})
        row = next(row for row in range(2) if self.window.table.item(row, 0).data(ACTIVE_ROLE))
        self.assertEqual(self.window.table.item(row, 4).text(), "正在安装")
        self.assertTrue(self.window.spinner_timer.isActive())
        self.assertIn("当前应用", self.window.activity_title.text())
        self.assertEqual(self.window.stat_numbers[-1].text(), "1")
        self.assertEqual(self.window.pending_stat_title.text(), "排队等待")
        self.assertEqual(self.window.table.item(1 - row, 4).text(), "排队等待")
        self.assertEqual(self.window.action.text(), "正在安装…")
        self.assertFalse(self.window.action.isEnabled())
        self.assertEqual(self.window.summary_title.text(), "当前安装")
        self.window.failure_filter.setCurrentIndex(1)
        self.window.show_failure_details()
        self.assertEqual(self.window.failure_table.rowCount(), 1)
        self.assertEqual(self.window.failure_table.item(0, 0).data(Qt.ItemDataRole.UserRole), second.package)
        self.window.handle_event({"kind": "result", "app": replace(pending[0], status="installed"), "completed": 1, "total": 2})
        self.assertIsNone(self.window.active_install)
        self.assertFalse(self.window.spinner_timer.isActive())
        self.assertEqual(self.window.table.item(row, 4).text(), "安装成功")
        self.assertTrue(self.window.table.item(row, 4).icon().isNull())
        self.assertEqual(first.status, "exported")

    def test_spinner_tick_updates_locally_without_touching_list_or_progress(self) -> None:
        """动画帧不重建表格、不推进虚假进度，并在长时间等待时给出手机确认提示。"""
        self.window.mode = "library"
        app = App("com.test.active", files=["/backup/active.apk"], status="exported")
        self.window.set_library_records([app])
        self.window.job_kind = "install"
        self.window.failure_records = [replace(app, status="pending", operation="install")]
        self.window.handle_event({"kind": "progress", "app": app, "completed": 2, "total": 56})
        self.window.spinner_timer.stop()
        row_item = self.window.table.item(0, 1)
        self.window.install_started = 100
        with patch("hoardy_adb.gui.time.monotonic", return_value=195), patch.object(self.window, "apply_filter") as filter, patch.object(self.window, "set_records") as rebuild:
            self.window.tick_install_spinner()
        filter.assert_not_called()
        rebuild.assert_not_called()
        self.assertEqual(self.window.progress.value(), 2)
        self.assertEqual(self.window.activity_elapsed.text(), "已用 01:35")
        self.assertIn("手机上的安装确认", self.window.activity_note.text())
        self.assertIs(self.window.table.item(0, 1), row_item)
        self.window.handle_event({"kind": "paused", "reason": "device offline"})
        self.assertIsNone(self.window.active_install)
        self.assertFalse(self.window.spinner_timer.isActive())

    def test_resume_button_explains_missing_phone_and_binds_original(self) -> None:
        """恢复按钮区分没有手机、错误手机与原手机重连，不能让用户面对无原因的灰按钮。"""
        self.window.mode = "library"
        self.window.queue_target = "ORIGINAL"
        self.window.queue_session = "/library/install"
        self.window.failure_records = [App("com.test.pending", status="pending", operation="install")]
        self.window.update_failure_button()
        self.assertFalse(self.window.action.isEnabled())
        self.assertIn("尚未连接", self.window.action_reason.text())
        self.window.set_devices([Device("OTHER", "其他手机", "device")])
        self.assertFalse(self.window.action.isEnabled())
        self.assertIn("原来的新手机", self.window.action_reason.text())
        self.window.set_devices([Device("ORIGINAL", "荣耀", "device")])
        self.assertEqual(self.window.serial(True), "ORIGINAL")
        self.assertTrue(self.window.action.isEnabled())
        self.assertIn("继续剩余 1", self.window.action.text())
        with patch.object(self.window, "begin_batch") as start:
            self.window.action.click()
        self.assertEqual(start.call_args.args[0][0].package, "com.test.pending")
        self.assertEqual(start.call_args.args[2], Path(self.window.queue_session))

    def test_library_result_overlay_preserves_backup_and_failure_filter(self) -> None:
        """安装结果只覆盖显示，不能损坏备份对象；失败筛选和分组数量需一致。"""
        self.window.mode = "library"
        backup = App("com.test.fail", files=["/backup/base.apk"], status="exported")
        self.window.set_library_records([backup])
        result = App(backup.package, files=list(backup.files), status="failed", error="failure", operation="install")
        self.window.failure_records = [result]
        self.window.update_failure_button()
        self.window.filter.setCurrentIndex(2)
        self.assertFalse(self.window.table.isRowHidden(0))
        self.assertEqual(self.window.table.item(0, 4).text(), "失败")
        self.assertEqual(backup.status, "exported")
        self.assertEqual([number.text() for number in self.window.stat_numbers], ["0", "0", "1", "0"])

    def test_library_all_batches_and_individual_selection(self) -> None:
        """两次导出的应用可合并全选，也能切换到任一批次且清除旧选择。"""
        self.window.mode = "library"
        old = App("com.test.old", files=["/old/old.apk"], status="exported", session="/library/2026-01")
        new = App("com.test.new", files=["/new/new.apk"], status="exported", session="/library/2026-02")
        self.window.set_library_records([new, old])
        self.window.select_visible(True)
        self.assertEqual({app.package for app in self.window.selected_apps()}, {old.package, new.package})
        self.window.batch_filter.setCurrentIndex(self.window.batch_filter.findData(old.session))
        self.assertEqual(self.window.records, [old])
        self.assertFalse(self.window.selected_apps())
        self.window.select_visible(True)
        self.assertEqual(self.window.selected_apps(), [old])
        self.window.batch_filter.setCurrentIndex(0)
        self.assertEqual(len(self.window.records), 2)
        self.assertFalse(self.window.selected_apps())

    def test_library_all_prefers_valid_backup_and_batch_keeps_own_copy(self) -> None:
        """全部视图避免重复安装并回退有效旧副本，单批次仍准确显示本次记录。"""
        self.window.mode = "library"
        old = App("com.test.same", files=["/old/base.apk"], status="exported", session="/library/2026-01")
        new = App("com.test.same", files=["/new/base.apk"], status="unavailable", session="/library/2026-02")
        self.window.set_library_records([new, old])
        self.assertEqual(self.window.records, [old])
        self.window.batch_filter.setCurrentIndex(self.window.batch_filter.findData(new.session))
        self.assertEqual(self.window.records, [new])
        self.window.select_visible(True)
        self.assertFalse(self.window.selected_apps())

    def test_install_shortcut_opens_all_batches_instead_of_starting_install(self) -> None:
        """下一步必须先进入全部应用库让用户选择，不能直接启动最近一批安装。"""
        with patch.object(self.window, "start_job") as start, patch.object(self.window, "begin_batch") as install:
            self.window.install_last_export()
        self.assertEqual(self.window.mode, "library")
        self.assertEqual(self.window.batch_filter.currentData(), "")
        start.assert_called_once()
        install.assert_not_called()

    def test_unavailable_app_never_selected(self) -> None:
        """不可导出的应用不能被表头全选选中。"""
        self.window.header_clicked(0)
        self.assertEqual(len(self.window.selected_apps()), 3)
        self.assertNotIn("com.test.bad", self.window.selected["phone"])

    def test_sort_sizes_numerically(self) -> None:
        """大小列按字节排序而不是按 10 和 2 的字符串顺序排序。"""
        self.window.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        keys = [self.window.table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(4)]
        self.assertEqual(keys, ["com.test.bad", "com.test.chat", "com.test.map", "com.test.video"])

    def test_metadata_update_preserves_other_rows_and_selection(self) -> None:
        """后台补资料只更新对应应用，不重建整张表或清空用户已勾选项。"""
        self.window.phone_records = list(self.apps)
        self.window.selected["phone"].add("com.test.chat")
        untouched = next(self.window.table.item(row, 1) for row in range(4)
                         if self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "com.test.map")
        updated = App("com.test.chat", name="新的应用名称", version="2.0", size=3 * 1024**2)
        self.window.handle_event({"kind": "app", "app": updated, "completed": 1, "total": 4})
        remaining = next(self.window.table.item(row, 1) for row in range(4)
                         if self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "com.test.map")
        self.assertIs(untouched, remaining)
        self.assertIn("com.test.chat", self.window.selected["phone"])
        self.assertEqual(self.window.selected_apps()[0].name, "新的应用名称")

    def test_export_can_interrupt_background_metadata_scan(self) -> None:
        """已选应用可以直接请求导出，不需要等另外一百多个应用读完资料。"""
        self.window.set_devices([Device("OLD", "红魔", "device")])
        worker = Mock()
        worker.stop = threading.Event()
        self.window.worker, self.window.job_kind = worker, "scan"
        self.window.selected["phone"].add("com.test.chat")
        self.window.update_summary()
        self.assertTrue(self.window.action.isEnabled())
        self.window.begin_batch(self.window.selected_apps(), "export")
        self.assertTrue(worker.stop.is_set())
        self.assertEqual(self.window.pending_export[0].package, "com.test.chat")
        self.window.worker = None

    def test_two_devices_get_distinct_roles(self) -> None:
        """同时连接两台手机时必须为来源和目标选择不同的设备。"""
        self.window.set_devices([Device("OLD", "红魔", "device"), Device("NEW", "荣耀", "device")])
        self.assertEqual(self.window.serial(), "OLD")
        self.assertEqual(self.window.serial(True), "NEW")

    def test_single_cable_swap_selects_new_phone_as_target(self) -> None:
        """拔掉旧手机再连接新手机时，不得自动把新手机当成来源扫描。"""
        self.window.set_devices([Device("OLD", "红魔", "device")])
        self.window.source_serial = "OLD"
        self.window.set_devices([])
        self.window.set_devices([Device("NEW", "荣耀", "device")])
        self.window.last_export = [self.apps[0]]
        self.window.update_summary()
        self.assertEqual(self.window.serial(), "")
        self.assertEqual(self.window.serial(True), "NEW")
        self.assertTrue(self.window.next_button.isEnabled())

    def test_library_can_install_with_only_one_connected_phone(self) -> None:
        """从本地应用库安装不要求旧手机同时在线。"""
        self.window.set_devices([Device("NEW", "荣耀", "device")])
        with patch.object(Window, "load_view"):
            self.window.change_view(1)
        self.assertEqual(self.window.serial(), "")
        self.assertEqual(self.window.serial(True), "NEW")

    def test_unauthorized_device_shows_instruction(self) -> None:
        """未授权设备不能成为操作目标，并显示手机端授权提示。"""
        self.window.set_devices([Device("OLD", "红魔", "unauthorized")])
        self.assertEqual(self.window.serial(), "")
        self.assertIn("USB 调试", self.window.connection.text())

    def test_same_device_disables_install(self) -> None:
        """目标与来源相同时禁用下一步安装按钮。"""
        self.window.set_devices([Device("OLD", "红魔", "device")])
        self.window.target.setCurrentIndex(self.window.target.findData("OLD"))
        self.window.mode = "library"
        self.window.selected["library"].add(self.apps[0].key)
        self.window.update_summary()
        self.assertFalse(self.window.next_button.isEnabled())

    def test_library_install_button_uses_current_batch_selection(self) -> None:
        """应用库右侧按钮直接交付当前批次选择，不能重新加载并清空勾选。"""
        self.window.set_devices([Device("OLD", "红魔", "device"), Device("NEW", "荣耀", "device")])
        self.window.mode = "library"
        backup = App("com.test.backup", files=["/backup/base.apk"], status="exported", session="/backup/old")
        self.window.set_library_records([backup])
        self.window.batch_filter.setCurrentIndex(1)
        self.assertFalse(self.window.next_button.isEnabled())
        self.assertEqual(self.window.next_button.text(), "请先勾选应用")
        self.window.select_visible(True)
        self.assertTrue(self.window.next_button.isEnabled())
        self.assertIn("1", self.window.next_button.text())
        with patch.object(self.window, "begin_batch") as install, patch.object(self.window, "load_view") as reload:
            self.window.next_button.click()
        install.assert_called_once_with([backup], "install")
        reload.assert_not_called()
        self.assertEqual(self.window.batch_filter.currentData(), backup.session)
        self.assertEqual(self.window.selected_apps(), [backup])
        # 使用真实按钮到确认弹窗的路径，取消后不能启动安装或改变选择。
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as confirm, patch.object(self.window, "start_job") as start:
            self.window.next_button.click()
        confirm.assert_called_once()
        self.assertIn("荣耀", confirm.call_args.args[2])
        self.assertIn("1 个应用", confirm.call_args.args[2])
        start.assert_not_called()
        self.assertEqual(self.window.selected_apps(), [backup])

    def test_default_show_all_and_toggle_exported_visibility(self) -> None:
        """默认全部显示；隐藏已导出可以随时恢复，且不自动重新勾选已取消项。"""
        chat = self.apps[0]
        self.deliver_index({chat.package: [self.backup(chat)]})
        self.assertFalse(self.window.hide_exported.isChecked())
        self.assertEqual(len(self.visible_packages()), 4)
        self.window.table.item(self.row_for(chat.package), 0).setCheckState(Qt.CheckState.Checked)
        self.window.hide_exported.setChecked(True)
        self.assertNotIn(chat.package, self.visible_packages())
        self.assertNotIn(chat.package, self.window.selected["phone"])
        self.window.hide_exported.setChecked(False)
        self.assertIn(chat.package, self.visible_packages())
        self.assertEqual(self.window.table.item(self.row_for(chat.package), 0).checkState(), Qt.CheckState.Unchecked)

    def test_visible_select_all_partial_and_clear_hidden_choices(self) -> None:
        """明确的全选控件遵守隐藏条件，清空选择包含普通搜索隐藏项。"""
        chat = self.apps[0]
        self.deliver_index({chat.package: [self.backup(chat)]})
        self.window.hide_exported.setChecked(True)
        self.window.select_all.click()
        self.assertEqual(self.window.selected["phone"], {"com.test.video", "com.test.map"})
        self.assertEqual(self.window.select_all.checkState(), Qt.CheckState.Checked)
        self.window.table.item(self.row_for("com.test.map"), 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(self.window.select_all.checkState(), Qt.CheckState.PartiallyChecked)
        self.window.select_all.click()
        self.assertEqual(len(self.window.selected["phone"]), 2)
        self.window.search.setText("视频")
        self.assertIn("com.test.map", self.window.selected["phone"])
        self.window.clear_button.click()
        self.assertEqual(self.window.selected["phone"], set())

    def test_async_index_prunes_hidden_choice_and_pending_request(self) -> None:
        """先选核对中应用，索引确认已导出后必须取消隐藏选择及尚未启动的请求。"""
        chat = self.apps[0]
        self.window.export_index_ready = False
        self.window.apply_filter()
        self.window.hide_exported.setChecked(True)
        self.window.table.item(self.row_for(chat.package), 0).setCheckState(Qt.CheckState.Checked)
        self.window.pending_export = [chat]
        self.deliver_index({chat.package: [self.backup(chat)]})
        self.assertNotIn(chat.package, self.visible_packages())
        self.assertNotIn(chat.package, self.window.selected["phone"])
        self.assertEqual(self.window.pending_export, [])

    def test_async_version_resolution_also_prunes_hidden_choice(self) -> None:
        """索引先就绪、版本后补齐的另一种时序，同样不能保留隐藏选择。"""
        chat = self.apps[0]
        self.deliver_index({chat.package: [self.backup(chat)]})
        unknown = App(chat.package, name=chat.name, status="loading")
        self.window.update_phone_app(unknown)
        self.window.hide_exported.setChecked(True)
        self.window.table.item(self.row_for(chat.package), 0).setCheckState(Qt.CheckState.Checked)
        self.window.pending_export = [unknown]
        self.window.handle_event({"kind": "app", "app": chat, "completed": 1, "total": 4})
        self.assertNotIn(chat.package, self.visible_packages())
        self.assertEqual(self.window.pending_export, [])
        self.assertEqual(self.window.selected["phone"], set())

    def test_counts_include_unknown_version_separately(self) -> None:
        """所有应用必须且只能归入三个统计之一，核对中不能被隐藏。"""
        chat, video = self.apps[:2]
        self.deliver_index({chat.package: [self.backup(chat)], video.package: [App(video.package, files=["/test/unknown.apk"])]})
        self.assertIn("共 4 个 · 已导出 1 个 · 未导出 2 个 · 核对中 1 个", self.window.count.text())
        self.window.hide_exported.setChecked(True)
        self.assertIn(video.package, self.visible_packages())
        self.assertEqual(len(self.visible_packages()), 3)

    def test_success_clears_package_selection_and_preserves_phone_identity(self) -> None:
        """导出后键已变成文件路径，仍需按包名取消勾选，手机对象不能变为备份对象。"""
        chat = self.apps[0]
        self.window.selected["phone"].add(chat.package)
        self.window.set_records(self.apps)
        result = self.backup(chat, "20")
        self.window.job_kind = "export"
        self.window.handle_event({"kind": "result", "app": result, "completed": 1, "total": 1})
        current = next(app for app in self.window.phone_records if app.package == chat.package)
        self.assertEqual(current.key, chat.package)
        self.assertEqual(current.files, [])
        self.assertEqual(current.version_code, "20")
        self.assertNotIn(chat.package, self.window.selected["phone"])
        self.assertEqual(self.window.table.item(self.row_for(chat.package), 4).text(), "已导出")

    def test_failed_item_remains_selected_after_other_item_succeeds(self) -> None:
        """部分成功只取消成功项的勾选，失败项仍可直接重试。"""
        chat, video = self.apps[:2]
        self.window.selected["phone"].update([chat.package, video.package])
        self.window.set_records(self.apps)
        self.window.job_kind = "export"
        self.window.handle_event({"kind": "result", "app": self.backup(chat), "completed": 1, "total": 2})
        self.window.handle_event({"kind": "result", "app": App(video.package, status="failed"), "completed": 2, "total": 2})
        self.assertEqual(self.window.selected["phone"], {video.package})

    def test_status_sort_and_hide_use_stable_package_identity(self) -> None:
        """状态排序会移动行，隐藏结果仍必须对应正确应用。"""
        self.window.table.sortItems(4, Qt.SortOrder.DescendingOrder)
        chat = self.apps[0]
        self.deliver_index({chat.package: [self.backup(chat)]})
        self.window.hide_exported.setChecked(True)
        self.assertEqual(self.visible_packages(), {app.package for app in self.apps[1:]})

    def test_library_change_clears_old_index_and_rejects_late_result(self) -> None:
        """切换目录立即清空旧状态，旧后台结果不能重新污染新目录的标记。"""
        old = self.window.library
        chat = self.apps[0]
        self.deliver_index({chat.package: [self.backup(chat)]})
        self.window.hide_exported.setChecked(True)
        with patch("hoardy_adb.gui.QFileDialog.getExistingDirectory", return_value=self.temp.name), patch.object(Window, "refresh_export_index") as refresh:
            self.window.choose_library()
        refresh.assert_called_once()
        self.assertEqual(self.window.export_index, {})
        self.assertFalse(self.window.export_index_ready)
        self.assertEqual(len(self.visible_packages()), 4)
        self.window.handle_event({"kind": "export_index", "index": {chat.package: [self.backup(chat)]},
                                  "library": str(old.resolve())})
        self.assertFalse(self.window.export_index_ready)

    def test_reopened_window_recovers_export_status_from_existing_manifest(self) -> None:
        """新窗口从现有清单恢复状态，隐藏设置仍默认关闭，不依赖上次内存。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "batch"
            batch.mkdir()
            file = batch / "backup__com.test.chat.apk"
            file.write_bytes(b"local exported test artifact")
            backup = self.backup(self.apps[0])
            backup.files, backup.size = [str(file)], file.stat().st_size
            apk.save_session(batch, [backup], "OLD", "export")
            reopened = Window()
            reopened.timer.stop()
            try:
                reopened.library = root
                reopened.phone_records = list(self.apps)
                reopened.set_records(self.apps)
                reopened.library_job(False, reopened.handle_event, threading.Event())
                self.assertTrue(reopened.export_index_ready)
                self.assertIn("已导出 1 个", reopened.count.text())
                self.assertFalse(reopened.hide_exported.isChecked())
            finally:
                reopened.close()

    def test_empty_pending_export_does_not_start_after_scan(self) -> None:
        """后台核对取消了全部排队项后，不应在扫描结束时启动空导出任务。"""
        self.window.worker = Mock()
        self.window.job_kind = "scan"
        self.window.pending_export = []
        with patch.object(Window, "begin_batch") as begin:
            self.window.job_finished()
        begin.assert_not_called()
        self.assertIsNone(self.window.pending_export)
        self.assertIn("无需重复导出", self.window.status.text())


if __name__ == "__main__":
    unittest.main()
