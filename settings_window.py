"""Native model, behavior, and permission settings for Voice Input."""

from __future__ import annotations

import threading
from copy import deepcopy
from urllib.parse import urlparse

from macos_context import accessibility_is_trusted
from voice_input_core import configured_api_key


_ACTION_CLASS = None


def _action_class():
    global _ACTION_CLASS
    if _ACTION_CLASS is not None:
        return _ACTION_CLASS

    import objc
    from Foundation import NSObject

    class VoiceInputSettingsActions(NSObject):
        @objc.IBAction
        def save_(self, _sender):
            self.controller.save()

        @objc.IBAction
        def testASR_(self, _sender):
            self.controller.test_connection("asr")

        @objc.IBAction
        def testLLM_(self, _sender):
            self.controller.test_connection("llm")

        @objc.IBAction
        def openAccessibility_(self, _sender):
            self.controller.open_accessibility_settings()

        @objc.IBAction
        def openMicrophone_(self, _sender):
            self.controller.open_microphone_settings()

    _ACTION_CLASS = VoiceInputSettingsActions
    return _ACTION_CLASS


def microphone_permission_label() -> str:
    try:
        from AVFoundation import (
            AVAuthorizationStatusAuthorized,
            AVAuthorizationStatusDenied,
            AVAuthorizationStatusNotDetermined,
            AVCaptureDevice,
            AVMediaTypeAudio,
        )

        status = AVCaptureDevice.authorizationStatusForMediaType_(
            AVMediaTypeAudio
        )
        if status == AVAuthorizationStatusAuthorized:
            return "已授权"
        if status == AVAuthorizationStatusDenied:
            return "未授权"
        if status == AVAuthorizationStatusNotDetermined:
            return "首次录音时询问"
        return "受系统限制"
    except Exception:
        return "待检查"


class SettingsController:
    """Own a single reusable AppKit settings window."""

    OUTPUT_MODES = ("auto", "中文", "English")

    def __init__(self, config_store, on_saved=None):
        self.config_store = config_store
        self.on_saved = on_saved
        self.window = None
        self.controls: dict[str, object] = {}
        self.action_target = None
        self.status_label = None

    def show(self) -> None:
        if self.window is None:
            self._build()
        self.reload()

        from AppKit import (
            NSApplication,
            NSApplicationActivateIgnoringOtherApps,
        )

        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezelStyleRounded,
            NSButton,
            NSButtonTypeSwitch,
            NSClosableWindowMask,
            NSColor,
            NSFont,
            NSMakeRect,
            NSMiniaturizableWindowMask,
            NSPopUpButton,
            NSSecureTextField,
            NSTextField,
            NSTitledWindowMask,
        )

        width, height = 620.0, 570.0
        style = (
            NSTitledWindowMask
            | NSClosableWindowMask
            | NSMiniaturizableWindowMask
        )
        self.window = (
            __import__("AppKit")
            .NSWindow.alloc()
            .initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(240, 180, width, height),
                style,
                NSBackingStoreBuffered,
                False,
            )
        )
        self.window.setTitle_("语音输入设置")
        self.window.setReleasedWhenClosed_(False)
        content = self.window.contentView()

        def label(x, y, w, text, *, bold=False, color=None):
            view = NSTextField.alloc().initWithFrame_(
                NSMakeRect(x, y, w, 22)
            )
            view.setStringValue_(text)
            view.setBezeled_(False)
            view.setDrawsBackground_(False)
            view.setEditable_(False)
            view.setSelectable_(False)
            view.setFont_(
                NSFont.boldSystemFontOfSize_(13)
                if bold
                else NSFont.systemFontOfSize_(12)
            )
            if color is not None:
                view.setTextColor_(color)
            content.addSubview_(view)
            return view

        def field(key, x, y, w, secure=False):
            klass = NSSecureTextField if secure else NSTextField
            view = klass.alloc().initWithFrame_(NSMakeRect(x, y, w, 25))
            content.addSubview_(view)
            self.controls[key] = view
            return view

        def button(title, action, x, y, w):
            view = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 28))
            view.setTitle_(title)
            view.setBezelStyle_(NSBezelStyleRounded)
            view.setTarget_(self.action_target)
            view.setAction_(action)
            content.addSubview_(view)
            return view

        def checkbox(key, title, x, y, w):
            view = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
            view.setButtonType_(NSButtonTypeSwitch)
            view.setTitle_(title)
            content.addSubview_(view)
            self.controls[key] = view

        action_class = _action_class()
        self.action_target = action_class.alloc().init()
        self.action_target.controller = self

        label(20, 532, 580, "模型连接", bold=True)
        label(20, 505, 90, "语音识别")
        field("asr_provider", 110, 503, 130)
        field("asr_base_url", 250, 503, 250)
        button("测试", "testASR:", 510, 501, 88)
        label(20, 474, 90, "ASR 模型")
        field("asr_model", 110, 472, 190)
        label(310, 474, 60, "API Key")
        asr_key = field("asr_key", 370, 472, 228, secure=True)
        asr_key.setPlaceholderString_("留空表示保留 Keychain 中的值")

        label(20, 431, 90, "文字整理")
        field("llm_provider", 110, 429, 130)
        field("llm_base_url", 250, 429, 250)
        button("测试", "testLLM:", 510, 427, 88)
        label(20, 400, 90, "LLM 模型")
        field("llm_model", 110, 398, 190)
        label(310, 400, 60, "API Key")
        llm_key = field("llm_key", 370, 398, 228, secure=True)
        llm_key.setPlaceholderString_("留空表示保留 Keychain 中的值")

        label(20, 352, 580, "输入行为", bold=True)
        label(20, 321, 90, "输出语言")
        output_mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(110, 318, 160, 28), False
        )
        output_mode.addItemsWithTitles_(["自动判断", "中文", "English"])
        content.addSubview_(output_mode)
        self.controls["output_mode"] = output_mode
        checkbox("auto_paste", "结束识别后自动写入原输入框", 300, 320, 250)
        checkbox("restore_clipboard", "写入后恢复原剪贴板", 300, 292, 250)
        checkbox("realtime_preview", "录音时显示实时识别预览", 300, 264, 250)

        label(20, 283, 90, "浮窗宽度")
        field("panel_width", 110, 281, 80)
        label(200, 283, 45, "高度")
        field("panel_height", 245, 281, 60)
        label(20, 252, 90, "光标间距")
        field("caret_gap", 110, 250, 80)
        label(
            200,
            252,
            95,
            "px（建议 52）",
            color=NSColor.secondaryLabelColor(),
        )

        label(20, 205, 580, "系统权限", bold=True)
        self.permission_label = label(20, 174, 320, "")
        button("辅助功能设置", "openAccessibility:", 350, 170, 118)
        button("麦克风设置", "openMicrophone:", 478, 170, 120)

        self.status_label = label(
            20,
            106,
            440,
            "",
            color=NSColor.secondaryLabelColor(),
        )
        label(
            20,
            76,
            560,
            "API Key 只保存在 macOS Keychain，不写入配置文件。",
            color=NSColor.secondaryLabelColor(),
        )
        button("保存设置", "save:", 480, 30, 118)

    def reload(self) -> None:
        config = self.config_store.load()
        asr = config.get("asr", {})
        llm = config.get("llm", {})
        ui = config.get("ui", {})
        preview = config.get("realtime_preview", {})
        for key, value in (
            ("asr_provider", asr.get("provider", "")),
            ("asr_base_url", asr.get("base_url", "")),
            ("asr_model", asr.get("model", "")),
            ("llm_provider", llm.get("provider", "")),
            ("llm_base_url", llm.get("base_url", "")),
            ("llm_model", llm.get("model", "")),
            ("panel_width", ui.get("panel_width", 340)),
            ("panel_height", ui.get("panel_height", 170)),
            ("caret_gap", ui.get("caret_gap", 52)),
        ):
            self.controls[key].setStringValue_(str(value))
        self.controls["asr_key"].setStringValue_("")
        self.controls["llm_key"].setStringValue_("")
        mode = config.get("output", {}).get("mode", "auto")
        self.controls["output_mode"].selectItemAtIndex_(
            self.OUTPUT_MODES.index(mode) if mode in self.OUTPUT_MODES else 0
        )
        self.controls["auto_paste"].setState_(
            1 if config.get("auto_paste", True) else 0
        )
        self.controls["restore_clipboard"].setState_(
            1 if config.get("restore_clipboard", True) else 0
        )
        self.controls["realtime_preview"].setState_(
            1 if preview.get("enabled", True) else 0
        )
        ax = "已授权" if accessibility_is_trusted() else "未授权"
        mic = microphone_permission_label()
        self.permission_label.setStringValue_(
            f"辅助功能：{ax}    麦克风：{mic}"
        )
        self.status_label.setStringValue_("")

    def _field(self, name: str) -> str:
        return str(self.controls[name].stringValue()).strip()

    @staticmethod
    def _number(value: str, name: str, minimum: float) -> float:
        try:
            number = float(value)
        except ValueError as error:
            raise ValueError(f"{name}必须是数字") from error
        if number < minimum:
            raise ValueError(f"{name}不能小于 {minimum:g}")
        return number

    def _settings_patch(self) -> dict:
        asr_url = self._field("asr_base_url")
        llm_url = self._field("llm_base_url")
        for name, value in (("ASR 地址", asr_url), ("LLM 地址", llm_url)):
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"{name}不是有效的 HTTP(S) 地址")
        mode_index = int(self.controls["output_mode"].indexOfSelectedItem())
        return {
            "asr": {
                "provider": self._field("asr_provider") or "OpenAI 兼容",
                "base_url": asr_url.rstrip("/"),
                "model": self._field("asr_model"),
                "api_key": "",
            },
            "llm": {
                "provider": self._field("llm_provider") or "OpenAI 兼容",
                "base_url": llm_url.rstrip("/"),
                "model": self._field("llm_model"),
                "api_key": "",
            },
            "output": {"mode": self.OUTPUT_MODES[mode_index]},
            "auto_paste": bool(self.controls["auto_paste"].state()),
            "restore_clipboard": bool(
                self.controls["restore_clipboard"].state()
            ),
            "realtime_preview": {
                "enabled": bool(self.controls["realtime_preview"].state())
            },
            "ui": {
                "panel_width": self._number(
                    self._field("panel_width"), "浮窗宽度", 340
                ),
                "panel_height": self._number(
                    self._field("panel_height"), "浮窗高度", 170
                ),
                "caret_gap": self._number(
                    self._field("caret_gap"), "光标间距", 8
                ),
            },
            "onboarding": {"completed": True},
        }

    def save(self) -> None:
        try:
            config = self.config_store.save_credentials(
                self._settings_patch(),
                asr_secret=self._field("asr_key") or None,
                llm_secret=self._field("llm_key") or None,
            )
            self.controls["asr_key"].setStringValue_("")
            self.controls["llm_key"].setStringValue_("")
            self.status_label.setStringValue_("✅ 设置已保存并生效")
            if self.on_saved:
                self.on_saved(config)
        except Exception as error:
            self.status_label.setStringValue_(f"⚠️ {error}")

    def test_connection(self, section_name: str) -> None:
        try:
            config = self.config_store.load()
            patch = self._settings_patch()
            section = deepcopy(config[section_name])
            section.update(patch[section_name])
            entered = self._field(f"{section_name}_key")
            key = entered or configured_api_key(section)
            if not key:
                raise ValueError("请先填写 API Key")
        except Exception as error:
            self.status_label.setStringValue_(f"⚠️ {error}")
            return

        self.status_label.setStringValue_("正在测试连接…")

        def worker():
            message = ""
            try:
                from openai import OpenAI

                client = OpenAI(
                    api_key=key,
                    base_url=str(section["base_url"]),
                    timeout=15,
                    max_retries=0,
                )
                client.models.list()
                message = "✅ 连接及认证成功"
            except Exception as error:
                status = getattr(error, "status_code", None)
                if status in (404, 405):
                    message = "✅ 服务已连通（未开放模型列表接口）"
                else:
                    message = f"⚠️ 连接失败：{str(error)[:120]}"

            from PyObjCTools import AppHelper

            AppHelper.callAfter(self.status_label.setStringValue_, message)

        threading.Thread(
            target=worker,
            name=f"voice-input-test-{section_name}",
            daemon=True,
        ).start()

    @staticmethod
    def _open_settings_url(url: str) -> None:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    def open_accessibility_settings(self) -> None:
        accessibility_is_trusted(prompt=True)
        self._open_settings_url(
            "x-apple.systempreferences:com.apple.preference.security"
            "?Privacy_Accessibility"
        )

    def open_microphone_settings(self) -> None:
        self._open_settings_url(
            "x-apple.systempreferences:com.apple.preference.security"
            "?Privacy_Microphone"
        )
