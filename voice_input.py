#!/usr/bin/env python3
"""Menu-bar voice input for macOS with a caret-following result panel."""

from __future__ import annotations

import fcntl
import os
import sys
import tempfile
import threading
import time
import traceback
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from macos_context import (
    InputContext,
    accessibility_is_trusted,
    capture_input_context,
    focus_matches,
    get_caret_frame,
    restore_input_focus,
)
from voice_input_core import (
    ConfigStore,
    Recorder,
    ScreenBounds,
    SpeechPipeline,
    configured_api_key,
    copy_text,
    humanize_hotkey,
    panel_origin_for_caret,
    paste_text,
    resolve_output_language,
)
from settings_window import SettingsController


APP_DIR = Path(__file__).resolve().parent
RIGHT_OPTION_KEY_CODE = 61
ESCAPE_KEY_CODE = 53
RETURN_KEY_CODES = frozenset((36, 76))
KEYBOARD_SHORTCUT_MODIFIER_MASK = (
    (1 << 17)  # Shift
    | (1 << 18)  # Control
    | (1 << 19)  # Option
    | (1 << 20)  # Command
)


def is_plain_enter_event(key_code: int, event_flags: int) -> bool:
    return (
        key_code in RETURN_KEY_CODES
        and not event_flags & KEYBOARD_SHORTCUT_MODIFIER_MASK
    )


def status_with_icon(status: str, icon: str) -> str:
    status = status.strip()
    if not status or not icon or status.startswith(icon):
        return status
    return f"{icon} {status}"


def concise_error_message(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    lowered = message.lower()
    if "libsndfile" in lowered or "_soundfile_data" in lowered:
        return "音频编码组件加载失败，请更新应用"
    if "libportaudio" in lowered or "_sounddevice_data" in lowered:
        return "录音组件加载失败，请更新应用"
    return message


def configure_ssl_certificate() -> Path | None:
    """Restore a real CA path after py2app's SSL bootstrap runs."""
    configured = os.environ.get("VOICE_INPUT_SSL_CERT_FILE", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if getattr(sys, "frozen", False):
        candidates.append(
            Path.home()
            / "Library"
            / "Application Support"
            / "VoiceInput"
            / "cacert.pem"
        )
    try:
        import certifi

        candidates.append(Path(certifi.where()))
    except Exception:
        pass

    for candidate in candidates:
        if candidate.is_file():
            os.environ["SSL_CERT_FILE"] = str(candidate)
            os.environ.pop("SSL_CERT_DIR", None)
            return candidate
    return None


SSL_CERT_PATH = configure_ssl_certificate()


def default_config_path() -> Path:
    configured = os.environ.get("VOICE_INPUT_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "VoiceInput"
            / "config.json"
        )
    return APP_DIR / "config.json"


CONFIG_PATH = default_config_path()
LOCK_PATH = Path("/tmp/com.felix.voice-input.lock")
CONFIG_STORE = ConfigStore(CONFIG_PATH)


def frontmost_application_pid() -> int | None:
    """Capture the target app even when it exposes no AX focused element."""
    try:
        from AppKit import NSWorkspace

        application = NSWorkspace.sharedWorkspace().frontmostApplication()
        if application is None:
            return None
        return int(application.processIdentifier())
    except Exception:
        return None


def activate_context_application(context: InputContext | None) -> bool:
    """Restore the target app when its web input has no stable AX element."""
    if context is None or context.pid is None:
        return False
    try:
        from AppKit import (
            NSApplicationActivateAllWindows,
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )

        application = NSRunningApplication.runningApplicationWithProcessIdentifier_(
            context.pid
        )
        if application is None or application.isTerminated():
            return False
        return bool(
            application.activateWithOptions_(
                NSApplicationActivateAllWindows
                | NSApplicationActivateIgnoringOtherApps
            )
        )
    except Exception:
        return False


def paste_result_to_context(
    context: InputContext | None,
    text: str,
    restore_clipboard: bool = True,
    focus_timeout: float = 0.8,
) -> bool:
    """Restore the original input and issue an actual Command-V paste."""
    if not text:
        return False
    restored = restore_input_focus(context)
    if not restored:
        restored = activate_context_application(context)
    if not restored:
        copy_text(text)
        return False

    if context is not None and context.element is not None:
        deadline = time.monotonic() + max(0.0, focus_timeout)
        while time.monotonic() < deadline:
            if focus_matches(context):
                break
            time.sleep(0.04)
    else:
        # Web chat editors may expose no AX text element. Returning to their
        # frontmost app still restores the last DOM focus for Command-V.
        time.sleep(0.12)
    # App activation is asynchronous. AXFocused has already been requested,
    # so paste even when a web view cannot report element identity reliably.
    time.sleep(0.06)
    paste_text(text, restore_clipboard)
    return True


def prefer_external_input_context(
    captured: InputContext,
    previous: InputContext | None,
    own_pid: int,
) -> InputContext:
    """Never replace a real input target with this app's floating panel."""
    if captured.pid != own_pid:
        return captured
    if previous is not None and previous.pid != own_pid:
        return previous
    return captured


def load_config() -> dict:
    """Compatibility entry point used by selftest.py."""
    return CONFIG_STORE.load()


def transcribe(asr_config: dict, wav_path: Path) -> str:
    """Compatibility wrapper for the original self-test interface."""
    config = CONFIG_STORE.load()
    config["asr"] = asr_config
    return SpeechPipeline(config).transcribe(wav_path)


def polish(llm_config: dict, raw_text: str, target_language: str) -> str:
    """Compatibility wrapper for the original self-test interface."""
    config = CONFIG_STORE.load()
    config["llm"] = llm_config
    return SpeechPipeline(config).polish(raw_text, target_language)


def run_on_main(callback) -> None:
    from Foundation import NSThread
    from PyObjCTools import AppHelper

    if NSThread.isMainThread():
        callback()
    else:
        AppHelper.callAfter(callback)


def run_on_main_later(delay: float, callback) -> None:
    """Schedule AppKit/keyboard work after the current UI event returns."""

    def schedule() -> None:
        from PyObjCTools import AppHelper

        AppHelper.callLater(delay, callback)

    run_on_main(schedule)


_PANEL_TARGET_CLASS = None
_PANEL_BUTTON_CLASS = None
_PANEL_WINDOW_CLASS = None


def panel_target_class():
    global _PANEL_TARGET_CLASS
    if _PANEL_TARGET_CLASS is not None:
        return _PANEL_TARGET_CLASS

    import objc
    from Foundation import NSObject

    class VoiceInputPanelActions(NSObject):
        @objc.IBAction
        def languageChanged_(self, sender):
            if self.language_callback:
                self.language_callback(int(sender.selectedSegment()))

        @objc.IBAction
        def copyResult_(self, _sender):
            if self.copy_callback:
                self.copy_callback()

        @objc.IBAction
        def confirmInput_(self, _sender):
            print("[按钮] 完成点击", flush=True)
            if self.confirm_callback:
                self.confirm_callback()

    _PANEL_TARGET_CLASS = VoiceInputPanelActions
    return _PANEL_TARGET_CLASS


def panel_button_class():
    """Button that accepts the first click even when the app is inactive."""
    global _PANEL_BUTTON_CLASS
    if _PANEL_BUTTON_CLASS is not None:
        return _PANEL_BUTTON_CLASS

    from AppKit import NSButton

    class VoiceInputPanelButton(NSButton):
        def acceptsFirstMouse_(self, _event):
            return True

    _PANEL_BUTTON_CLASS = VoiceInputPanelButton
    return _PANEL_BUTTON_CLASS


def panel_window_class():
    """Floating window that is always eligible to become key and main."""
    global _PANEL_WINDOW_CLASS
    if _PANEL_WINDOW_CLASS is not None:
        return _PANEL_WINDOW_CLASS

    from AppKit import NSWindow

    class VoiceInputKeyWindow(NSWindow):
        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return True

    _PANEL_WINDOW_CLASS = VoiceInputKeyWindow
    return _PANEL_WINDOW_CLASS


class ResultPanel:
    """Key panel that shows transcription and safely captures confirmation."""

    MODES = ("auto", "中文", "English")

    def __init__(
        self,
        config: dict,
        language_callback,
        copy_callback,
        confirm_callback,
    ):
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezelBorder,
            NSBezelStyleRounded,
            NSButton,
            NSClosableWindowMask,
            NSColor,
            NSFloatingWindowLevel,
            NSFont,
            NSMakeRect,
            NSScrollView,
            NSSegmentSwitchTrackingSelectOne,
            NSSegmentedControl,
            NSTextField,
            NSTextView,
            NSTitledWindowMask,
        )

        ui_config = config.get("ui", {})
        width = max(340.0, float(ui_config.get("panel_width", 380)))
        height = max(170.0, float(ui_config.get("panel_height", 180)))
        self.caret_gap = max(8.0, float(ui_config.get("caret_gap", 52)))
        style = NSTitledWindowMask | NSClosableWindowMask
        window_class = panel_window_class()
        self.panel = window_class.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 400, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setTitle_("语音输入")
        self.panel.setLevel_(NSFloatingWindowLevel)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setReleasedWhenClosed_(False)
        content = self.panel.contentView()

        def make_label(x, y, w, h, text, bold=False, color=None):
            label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            label.setStringValue_(text)
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setFont_(
                NSFont.boldSystemFontOfSize_(12)
                if bold
                else NSFont.systemFontOfSize_(11)
            )
            if color is not None:
                label.setTextColor_(color)
            content.addSubview_(label)
            return label

        def make_text_area(x, y, w, h, font_size):
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(NSBezelBorder)
            text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
            text_view.setEditable_(False)
            text_view.setSelectable_(True)
            text_view.setFont_(NSFont.systemFontOfSize_(font_size))
            text_view.setBackgroundColor_(NSColor.textBackgroundColor())
            text_view.textContainer().setContainerSize_((w, 10_000))
            text_view.textContainer().setWidthTracksTextView_(True)
            scroll.setDocumentView_(text_view)
            content.addSubview_(scroll)
            return text_view

        column_gap = 8
        column_width = (width - 24 - column_gap) / 2
        self.status_label = make_label(
            12,
            height - 35,
            column_width,
            20,
            "原文 · 就绪",
            bold=True,
        )
        make_label(
            12 + column_width + column_gap,
            height - 35,
            30,
            20,
            "结果",
            bold=True,
        )
        self.mode_selector = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(width - 154, height - 38, 142, 26)
        )
        self.mode_selector.setSegmentCount_(3)
        for index, label in enumerate(("自动", "中文", "EN")):
            self.mode_selector.setLabel_forSegment_(label, index)
            self.mode_selector.setWidth_forSegment_(44, index)
        self.mode_selector.setTrackingMode_(NSSegmentSwitchTrackingSelectOne)
        content.addSubview_(self.mode_selector)

        self.raw_view = make_text_area(
            12,
            42,
            column_width,
            height - 84,
            12,
        )
        self.result_view = make_text_area(
            12 + column_width + column_gap,
            42,
            column_width,
            height - 84,
            13,
        )
        self.hint_label = make_label(
            12,
            10,
            width - 154,
            18,
            "右⌥ 录音/结束 · 自动写入 · Esc 取消",
            color=NSColor.tertiaryLabelColor(),
        )
        button_class = panel_button_class()
        self.copy_button = button_class.alloc().initWithFrame_(
            NSMakeRect(width - 142, 6, 58, 28)
        )
        self.copy_button.setTitle_("复制")
        self.copy_button.setBezelStyle_(NSBezelStyleRounded)
        content.addSubview_(self.copy_button)
        self.confirm_button = button_class.alloc().initWithFrame_(
            NSMakeRect(width - 76, 6, 64, 28)
        )
        self.confirm_button.setTitle_("写入")
        self.confirm_button.setBezelStyle_(NSBezelStyleRounded)
        content.addSubview_(self.confirm_button)

        target_class = panel_target_class()
        self.action_target = target_class.alloc().init()
        self.action_target.language_callback = language_callback
        self.action_target.copy_callback = copy_callback
        self.action_target.confirm_callback = confirm_callback
        self.mode_selector.setTarget_(self.action_target)
        self.mode_selector.setAction_("languageChanged:")
        self.copy_button.setTarget_(self.action_target)
        self.copy_button.setAction_("copyResult:")
        self.confirm_button.setTarget_(self.action_target)
        self.confirm_button.setAction_("confirmInput:")

        self.latest_result = ""
        self._last_position_signature = None
        self.set_mode(config.get("output", {}).get("mode", "auto"))

    def set_mode(self, mode: str) -> None:
        try:
            index = self.MODES.index(mode)
        except ValueError:
            index = 0
        run_on_main(lambda: self.mode_selector.setSelectedSegment_(index))

    def update(self, status=None, raw=None, result=None) -> None:
        if result is not None:
            self.latest_result = result

        def apply():
            if status is not None:
                self.status_label.setStringValue_(f"原文 · {status}")
            if raw is not None:
                self.raw_view.setString_(raw)
            if result is not None:
                self.result_view.setString_(result)

        run_on_main(apply)

    def show(self) -> None:
        def apply():
            # Keep the panel visible without activating it so the original
            # editor retains keyboard focus across displays and macOS Spaces.
            self.panel.orderFrontRegardless()

        run_on_main(apply)

    def move_near_context(self, context: InputContext | None) -> None:
        if context is None or context.pid == os.getpid():
            return
        # The panel itself may become focused after a click. Prefer the caret
        # rectangle captured before recording so subsequent timer/UI updates
        # cannot turn an AX element-frame fallback into a large window jump.
        frame = context.caret_frame or get_caret_frame(context.element)
        if not frame:
            return

        def apply():
            from AppKit import NSMakePoint, NSScreen

            screens = list(NSScreen.screens())
            if not screens:
                return
            bounds = []
            for screen in screens:
                visible = screen.visibleFrame()
                bounds.append(
                    ScreenBounds(
                        float(visible.origin.x),
                        float(visible.origin.y),
                        float(visible.size.width),
                        float(visible.size.height),
                    )
                )
            # NSScreen.mainScreen() follows the key window and therefore
            # changes when this floating panel crosses displays. AX global
            # coordinates are anchored to the menu-bar display, which AppKit
            # exposes as screens()[0], so use that fixed frame's top edge.
            coordinate_frame = screens[0].frame()
            coordinate_top = float(
                coordinate_frame.origin.y + coordinate_frame.size.height
            )
            panel_frame = self.panel.frame()
            origin = panel_origin_for_caret(
                frame,
                (panel_frame.size.width, panel_frame.size.height),
                bounds,
                coordinate_top,
                gap=self.caret_gap,
            )
            signature = (
                id(context),
                tuple(round(float(value), 2) for value in frame),
                tuple(
                    (
                        round(bound.x, 2),
                        round(bound.y, 2),
                        round(bound.width, 2),
                        round(bound.height, 2),
                    )
                    for bound in bounds
                ),
                round(float(panel_frame.size.width), 2),
                round(float(panel_frame.size.height), 2),
                round(self.caret_gap, 2),
            )
            if signature == self._last_position_signature:
                return
            self._last_position_signature = signature
            self.panel.setFrameOrigin_(NSMakePoint(*origin))

        run_on_main(apply)


class Phase(Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PROCESSING = "processing"
    PASTING = "pasting"
    CANCELLING = "cancelling"


@dataclass
class VoiceSession:
    context: InputContext
    target_language: str
    started_at: float
    cancel_event: threading.Event = field(default_factory=threading.Event)
    preview_stop: threading.Event = field(default_factory=threading.Event)
    preview_raw: str = ""
    preview_result: str = ""
    writeback_requested: bool = False


def right_option_transition(
    key_code: int,
    option_down: bool,
    was_down: bool,
) -> tuple[bool, bool]:
    """Return whether right Option was newly pressed and its next state."""
    if key_code != RIGHT_OPTION_KEY_CODE:
        return False, was_down
    if option_down and not was_down:
        return True, True
    return False, option_down


def start_optional_escape_monitor(start_callback) -> bool:
    """Keep the app alive while Accessibility permission is unavailable."""
    try:
        start_callback()
        return True
    except RuntimeError as error:
        print(
            f"[启动] Esc 拦截暂不可用：{error}；授权辅助功能后请重启应用",
            flush=True,
        )
        return False


class RecoveringRecorder(Recorder):
    """Retry CoreAudio with the current device rate after hardware changes."""

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self.recording:
                return
            self.frames = []

        preferred_rate = int(self.samplerate)
        rates = [preferred_rate]
        try:
            device = sd.query_devices(kind="input")
            default_rate = int(round(float(device["default_samplerate"])))
            if default_rate > 0 and default_rate not in rates:
                rates.append(default_rate)
        except Exception:
            default_rate = 48_000
            if default_rate not in rates:
                rates.append(default_rate)

        last_error = None
        for index, rate in enumerate(rates):
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                )
                stream.start()
            except Exception as error:
                last_error = error
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                if index + 1 < len(rates):
                    print(
                        f"[音频] {rate}Hz 打开失败，刷新设备后改用 "
                        f"{rates[index + 1]}Hz",
                        flush=True,
                    )
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass
                    continue
                raise

            with self._lock:
                self.samplerate = rate
                self.stream = stream
                self.recording = True
            if rate != preferred_rate:
                print(f"[音频] 已恢复，当前输入采样率 {rate}Hz", flush=True)
            return

        if last_error is not None:
            raise last_error


def make_app():
    import rumps

    class VoiceInputApp(rumps.App):
        def __init__(self, config: dict):
            super().__init__("🎙", quit_button=None)
            self.config = config
            self.recorder = RecoveringRecorder()
            self.phase = Phase.IDLE
            self.phase_lock = threading.RLock()
            self.session: VoiceSession | None = None
            self.listener = None
            self.pipeline: SpeechPipeline | None = None
            self.last_hotkey_at = 0.0
            self.last_elapsed_second = -1
            self.current_icon = "🎙"
            self.latest_result = ""
            self.latest_context: InputContext | None = None
            self.last_external_context: InputContext | None = None
            self.latest_language = "中文"
            self.hotkey_name = humanize_hotkey(config.get("hotkey", "<alt_r>"))

            self.panel_window = ResultPanel(
                config,
                language_callback=self._panel_language_changed,
                copy_callback=self.copy_latest_result,
                confirm_callback=self.on_confirm_button,
            )
            self.settings_controller = SettingsController(
                CONFIG_STORE,
                on_saved=self._settings_saved,
            )
            self.status_item = rumps.MenuItem(
                f"状态: 就绪（按{self.hotkey_name}开始）"
            )
            self.lang_items = {
                "auto": rumps.MenuItem(
                    "自动检测页面语言",
                    callback=lambda _sender: self.set_output_mode("auto"),
                ),
                "中文": rumps.MenuItem(
                    "固定输出中文",
                    callback=lambda _sender: self.set_output_mode("中文"),
                ),
                "English": rumps.MenuItem(
                    "固定输出 English",
                    callback=lambda _sender: self.set_output_mode("English"),
                ),
            }
            self.menu = [
                self.status_item,
                None,
                rumps.MenuItem("显示面板", callback=self.show_panel),
                rumps.MenuItem("复制上次结果", callback=self.copy_latest_result),
                rumps.MenuItem(
                    "输出语言",
                    [
                        self.lang_items["auto"],
                        self.lang_items["中文"],
                        self.lang_items["English"],
                    ],
                ),
                None,
                rumps.MenuItem("模型与应用设置…", callback=self.open_settings),
                rumps.MenuItem("打开高级配置文件", callback=self.open_config),
                rumps.MenuItem("重新加载配置", callback=self.reload_config),
                None,
                rumps.MenuItem("退出", callback=self.quit_app),
            ]
            self._sync_language_controls()
            self.timer = rumps.Timer(self._timer_tick, 0.25)
            self.timer.start()

        def set_listener(self, listener) -> None:
            self.listener = listener

        def _pipeline(self) -> SpeechPipeline:
            if self.pipeline is None:
                self.pipeline = SpeechPipeline(self.config)
            return self.pipeline

        def _set_ui(self, status=None, icon=None, raw=None, result=None) -> None:
            if result is not None:
                self.latest_result = result

            def apply():
                if icon is not None:
                    self.current_icon = icon
                    self.title = icon
                if status is not None:
                    display_status = status_with_icon(
                        status,
                        self.current_icon,
                    )
                    self.status_item.title = f"状态: {display_status}"
                else:
                    display_status = None
                self.panel_window.update(
                    status=display_status,
                    raw=raw,
                    result=result,
                )

            run_on_main(apply)

        def _sync_language_controls(self) -> None:
            mode = self.config.get("output", {}).get("mode", "auto")
            for key, item in self.lang_items.items():
                item.state = 1 if key == mode else 0
            self.panel_window.set_mode(mode)

        def _panel_language_changed(self, segment_index: int) -> None:
            modes = ("auto", "中文", "English")
            if 0 <= segment_index < len(modes):
                self.set_output_mode(modes[segment_index])

        def set_output_mode(self, mode: str) -> None:
            try:
                self.config = CONFIG_STORE.set_output_mode(mode)
                self.pipeline = None
                self._sync_language_controls()
                with self.phase_lock:
                    if self.session is not None:
                        self.session.target_language = resolve_output_language(
                            self.config.get("output", {}),
                            self.session.context.detected_language,
                        )
                    target = (
                        self.session.target_language
                        if self.session is not None
                        else {
                            "auto": "自动检测",
                            "中文": "中文",
                            "English": "English",
                        }[mode]
                    )
                self._set_ui(status=f"输出语言：{target}")
                print(f"[输出语言] {mode}", flush=True)
            except Exception as error:
                self._notify_error("无法切换输出语言", error)

        def on_hotkey(self) -> None:
            now = time.monotonic()
            if now - self.last_hotkey_at < 0.25:
                return
            self.last_hotkey_at = now
            print(f"[热键] {self.hotkey_name}", flush=True)
            action = None
            with self.phase_lock:
                if self.phase == Phase.IDLE:
                    self.phase = Phase.STARTING
                    action = "start"
                elif self.phase == Phase.RECORDING:
                    if self.session is not None:
                        self.session.writeback_requested = bool(
                            self.config.get("auto_paste", True)
                        )
                    action = "finish"
            if action == "start":
                self._begin_recording()
            elif action == "finish":
                self._request_finish("右 Option")
            else:
                self._set_ui(status="仍在处理中；按 Esc 可取消")

        def on_confirm_button(self) -> bool:
            return self._confirm_or_paste("写入按钮")

        def _confirm_or_paste(self, trigger: str) -> bool:
            action = None
            with self.phase_lock:
                if self.phase == Phase.RECORDING and self.session is not None:
                    action = "recording"
                elif self.phase in (Phase.STARTING, Phase.PROCESSING):
                    action = "processing"
                elif (
                    self.phase == Phase.IDLE
                    and self.latest_result
                    and self.latest_context is not None
                ):
                    self.phase = Phase.PASTING
                    action = "paste"
            if action == "recording":
                self._set_ui(status="请先按右 Option 结束录音", icon="🔴")
                return True
            if action == "processing":
                self._set_ui(status="正在生成最终结果，请稍候…", icon="⏳")
                return True
            if action == "paste":
                self._set_ui(status="正在返回原输入框并粘贴…", icon="⏳")
                # The button callback runs on AppKit's main queue. Let that
                # click finish, then keep app activation and keyboard-event
                # creation on the main queue. macOS Text Input Services traps
                # when those APIs are reached from a Python worker thread.
                run_on_main_later(
                    0.08,
                    lambda: self._paste_latest_result(trigger),
                )
                return True
            return False

        def _paste_latest_result(self, trigger: str) -> None:
            with self.phase_lock:
                context = self.latest_context
                text = self.latest_result
                language = self.latest_language
            try:
                self._writeback_result(context, text, language, trigger)
            except Exception as error:
                traceback.print_exc()
                self._notify_error("粘贴失败", error)
            finally:
                with self.phase_lock:
                    if self.phase == Phase.PASTING:
                        self.phase = Phase.IDLE

        def _writeback_result(
            self,
            context: InputContext | None,
            text: str,
            language: str,
            trigger: str,
        ) -> bool:
            pasted = paste_result_to_context(
                context,
                text,
                bool(self.config.get("restore_clipboard", True)),
            )
            with self.phase_lock:
                # A failed automatic writeback remains available for an
                # explicit retry via the 写入 button.
                if pasted and self.latest_result == text:
                    self.latest_result = ""
                    self.latest_context = None
            if pasted:
                self._set_ui(
                    status=f"已粘贴到原输入框 · {language}",
                    icon="✅",
                )
                print(f"[写回] {trigger}：已通过 Cmd+V 粘贴", flush=True)
                return True
            self._set_ui(
                status="结果已复制；聚焦输入框后可再点完成",
                icon="⚠️",
            )
            print(f"[写回] {trigger}：原输入框不可用，结果已复制", flush=True)
            return False

        def _request_finish(self, trigger: str) -> bool:
            with self.phase_lock:
                if self.phase != Phase.RECORDING or self.session is None:
                    return False
                self.phase = Phase.PROCESSING
                self.session.preview_stop.set()
            self._set_ui(
                status="正在完成最终识别，完成后自动写入…",
                icon="⏳",
            )
            print(f"[录音] {trigger} 请求完成", flush=True)
            threading.Thread(
                target=self._finish_pipeline,
                name="voice-input-pipeline",
                daemon=True,
            ).start()
            return True

        def on_escape(self) -> bool:
            print("[热键] Esc", flush=True)
            action = None
            with self.phase_lock:
                if self.phase == Phase.RECORDING:
                    self.phase = Phase.CANCELLING
                    if self.session is not None:
                        self.session.preview_stop.set()
                    action = "discard"
                elif self.phase in (Phase.STARTING, Phase.PROCESSING):
                    self.phase = Phase.CANCELLING
                    if self.session is not None:
                        self.session.cancel_event.set()
                        self.session.preview_stop.set()
                    action = "cancel"
                elif self.phase == Phase.IDLE and self.latest_result:
                    self.latest_result = ""
                    self.latest_context = None
                    action = "clear_result"
            if action == "discard":
                try:
                    self.recorder.stop()
                except Exception:
                    traceback.print_exc()
                with self.phase_lock:
                    self.phase = Phase.IDLE
                    self.session = None
                self._set_ui(status="已取消", icon="🎙")
                return True
            elif action == "cancel":
                self._set_ui(status="正在取消…", icon="🎙")
                return True
            elif action == "clear_result":
                self._set_ui(
                    status="已取消本次输入",
                    icon="🎙",
                    raw="",
                    result="",
                )
                return True
            return False

        def _begin_recording(self) -> None:
            print("[录音] 请求开始", flush=True)
            frontmost_pid = frontmost_application_pid()
            captured_context = capture_input_context()
            if (
                captured_context.pid is None
                and frontmost_pid is not None
                and frontmost_pid != os.getpid()
            ):
                captured_context.pid = frontmost_pid
                print("[焦点] 输入框无 AX 对象，使用前台应用回贴", flush=True)
            with self.phase_lock:
                previous_context = self.last_external_context
            context = prefer_external_input_context(
                captured_context,
                previous_context,
                os.getpid(),
            )
            if context is not captured_context:
                print("[焦点] 面板仍有焦点，复用上一次原输入框", flush=True)
            elif context.pid is not None and context.pid != os.getpid():
                with self.phase_lock:
                    self.last_external_context = context
            target = resolve_output_language(
                self.config.get("output", {}),
                context.detected_language,
            )
            session = VoiceSession(
                context=context,
                target_language=target,
                started_at=time.monotonic(),
            )
            try:
                self.recorder.start()
            except Exception as error:
                with self.phase_lock:
                    self.phase = Phase.IDLE
                self._notify_error("无法开始录音", error)
                return
            print("[录音] 已开始", flush=True)
            with self.phase_lock:
                if self.phase == Phase.CANCELLING:
                    self.recorder.stop()
                    self.phase = Phase.IDLE
                    self.session = None
                    self._set_ui(status="已取消", icon="🎙")
                    return
                self.session = session
                self.phase = Phase.RECORDING
                self.latest_result = ""
                self.latest_context = context
                self.latest_language = target
            self.last_elapsed_second = -1
            if self.config.get("ui", {}).get("follow_caret", True):
                self.panel_window.move_near_context(context)
            self.panel_window.show()
            self._set_ui(
                status=f"● 正在聆听 · 右 Option 结束 · 输出 {target}",
                icon="🔴",
                raw="",
                result="",
            )
            preview_config = self.config.get("realtime_preview", {})
            if bool(preview_config.get("enabled", True)):
                threading.Thread(
                    target=self._run_realtime_preview,
                    args=(session,),
                    name="voice-input-preview",
                    daemon=True,
                ).start()

        def _cancelled(self, session: VoiceSession) -> bool:
            return session.cancel_event.is_set()

        def _preview_active(self, session: VoiceSession) -> bool:
            with self.phase_lock:
                return (
                    self.session is session
                    and self.phase == Phase.RECORDING
                    and not session.preview_stop.is_set()
                )

        def _set_preview_ui(
            self,
            session: VoiceSession,
            *,
            status=None,
            raw=None,
            result=None,
        ) -> None:
            def apply_if_active():
                if self._preview_active(session):
                    self._set_ui(status=status, raw=raw, result=result)

            run_on_main(apply_if_active)

        def _run_realtime_preview(self, session: VoiceSession) -> None:
            preview_config = self.config.get("realtime_preview", {})
            first_update = max(
                0.4,
                float(preview_config.get("first_update_seconds", 1.2)),
            )
            interval = max(
                0.5,
                float(preview_config.get("interval_seconds", 2.0)),
            )
            minimum = max(
                0.4,
                float(preview_config.get("minimum_seconds", 0.8)),
            )
            if session.preview_stop.wait(first_update):
                return
            try:
                pipeline = SpeechPipeline(deepcopy(self.config))
            except Exception as error:
                print(f"[实时预览] 初始化失败：{error}", flush=True)
                return

            while self._preview_active(session):
                temp_file = tempfile.NamedTemporaryFile(
                    suffix=".wav",
                    prefix="voice_input_preview_",
                    delete=False,
                )
                wav_path = Path(temp_file.name)
                temp_file.close()
                try:
                    duration = self.recorder.snapshot(wav_path)
                    if duration >= minimum and self._preview_active(session):

                        def on_raw_partial(partial: str) -> None:
                            if partial:
                                session.preview_raw = partial
                                self._set_preview_ui(
                                    session,
                                    status="预览（未完成）· 正在识别…",
                                    raw=partial,
                                )

                        raw = pipeline.transcribe(
                            wav_path,
                            on_partial=on_raw_partial,
                        )
                        if raw and self._preview_active(session):
                            print(
                                f"[实时预览] {duration:.1f}s，"
                                f"识别 {len(raw)} 字",
                                flush=True,
                            )
                            session.preview_raw = raw
                            self._set_preview_ui(session, raw=raw)

                            def on_result_partial(partial: str) -> None:
                                if partial:
                                    session.preview_result = partial
                                    self._set_preview_ui(
                                        session,
                                        status=(
                                            f"预览（未完成）· 正在整理为 "
                                            f"{session.target_language}…"
                                        ),
                                        result=partial,
                                    )

                            polished = pipeline.polish(
                                raw,
                                session.target_language,
                                on_partial=on_result_partial,
                            )
                            if polished and self._preview_active(session):
                                session.preview_result = polished
                                self._set_preview_ui(
                                    session,
                                    raw=raw,
                                    result=polished,
                                )
                except Exception as error:
                    if self._preview_active(session):
                        print(f"[实时预览] 暂时不可用：{error}", flush=True)
                finally:
                    try:
                        wav_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                if session.preview_stop.wait(interval):
                    break

        def _finish_pipeline(self) -> None:
            with self.phase_lock:
                session = self.session
            if session is None:
                self._reset_idle()
                return

            temp_file = tempfile.NamedTemporaryFile(
                suffix=".wav",
                prefix="voice_input_",
                delete=False,
            )
            wav_path = Path(temp_file.name)
            temp_file.close()
            pipeline_started = time.monotonic()
            try:
                session.preview_stop.set()
                duration = self.recorder.stop(wav_path)
                print(f"[录音] 已停止，时长 {duration:.1f}s", flush=True)
                if self._cancelled(session):
                    return
                if duration < 0.4:
                    self._set_ui(status="录音太短，已忽略", icon="🎙")
                    return

                self._set_ui(status="正在识别语音…", icon="⏳")

                def on_raw_partial(partial: str) -> None:
                    if not self._cancelled(session):
                        self._set_ui(raw=partial)

                raw = self._pipeline().transcribe(
                    wav_path,
                    on_partial=on_raw_partial,
                )
                if self._cancelled(session):
                    return
                if not raw:
                    self._set_ui(status="没有识别到语音", icon="🎙")
                    return
                print(f"[转写] {raw}", flush=True)
                self._set_ui(
                    status=f"正在整理为 {session.target_language}…",
                    raw=raw,
                )

                def on_partial(partial: str) -> None:
                    if not self._cancelled(session):
                        self._set_ui(result=partial)

                polished = self._pipeline().polish(
                    raw,
                    session.target_language,
                    on_partial=on_partial,
                )
                if self._cancelled(session):
                    return
                if not polished:
                    raise RuntimeError("模型返回了空结果")
                print(f"[{session.target_language}] {polished}", flush=True)
                self._set_ui(result=polished)

                elapsed = time.monotonic() - pipeline_started
                with self.phase_lock:
                    self.latest_result = polished
                    self.latest_context = session.context
                    self.latest_language = session.target_language
                    writeback_requested = session.writeback_requested
                if writeback_requested:
                    self._set_ui(status="正在返回原输入框并粘贴…", icon="⏳")
                    run_on_main_later(
                        0.08,
                        lambda: self._writeback_result(
                            session.context,
                            polished,
                            session.target_language,
                            "自动写入",
                        ),
                    )
                else:
                    self._set_ui(
                        status=(
                            f"结果已准备好 · {session.target_language} · "
                            f"{elapsed:.1f}s · 点击写入"
                        ),
                        icon="✨",
                    )
                    print("[写回] 等待写入按钮确认", flush=True)
            except Exception as error:
                if not self._cancelled(session):
                    traceback.print_exc()
                    self._notify_error("处理失败", error)
            finally:
                try:
                    wav_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if self._cancelled(session):
                    self._set_ui(status="已取消", icon="🎙")
                self._reset_idle()

        def _reset_idle(self) -> None:
            with self.phase_lock:
                if self.session is not None:
                    self.session.preview_stop.set()
                self.phase = Phase.IDLE
                self.session = None

        def _timer_tick(self, _sender) -> None:
            with self.phase_lock:
                phase = self.phase
                session = self.session
            if session is None:
                return
            if phase == Phase.RECORDING:
                elapsed = int(time.monotonic() - session.started_at)
                if elapsed != self.last_elapsed_second:
                    self.last_elapsed_second = elapsed
                    self._set_ui(
                        status=(
                            f"● 正在聆听 {elapsed // 60:02d}:{elapsed % 60:02d}"
                            f" · 右 Option 结束 · 输出 {session.target_language}"
                        )
                    )

        def _notify_error(self, title: str, error: Exception) -> None:
            message = str(error).strip() or error.__class__.__name__
            display_message = concise_error_message(error)
            print(f"[错误] {title}：{message}", flush=True)
            self._set_ui(
                status=f"{title}：{display_message[:100]}",
                icon="🎙",
            )

            def notify():
                rumps.notification(
                    "语音输入",
                    title,
                    display_message[:200],
                )

            run_on_main(notify)
            self._reset_idle()

        def show_panel(self, _sender=None) -> None:
            with self.phase_lock:
                context = self.session.context if self.session else None
            if context:
                self.panel_window.move_near_context(context)
            self.panel_window.show()

        def copy_latest_result(self, _sender=None) -> None:
            text = self.latest_result or self.panel_window.latest_result
            if not text:
                self._set_ui(status="还没有可复制的结果")
                return
            copy_text(text)
            self._set_ui(status="结果已复制")

        def open_config(self, _sender=None) -> None:
            import subprocess

            subprocess.run(["open", "-t", str(CONFIG_PATH)], check=False)

        def open_settings(self, _sender=None) -> None:
            self.settings_controller.show()

        def schedule_settings(self) -> None:
            def show_once(timer):
                timer.stop()
                self.open_settings()

            self.settings_timer = rumps.Timer(show_once, 0.5)
            self.settings_timer.start()

        def _settings_saved(self, config: dict) -> None:
            self.config = config
            self.pipeline = None
            self.hotkey_name = humanize_hotkey(
                config.get("hotkey", "<alt_r>")
            )
            self._sync_language_controls()
            self._set_ui(status="设置已保存；模型配置已立即生效")

        def reload_config(self, _sender=None) -> None:
            try:
                self.config = CONFIG_STORE.load()
                self.pipeline = None
                self._sync_language_controls()
                self._set_ui(status="配置已重新加载")
            except Exception as error:
                self._notify_error("配置读取失败", error)

        def quit_app(self, _sender=None) -> None:
            with self.phase_lock:
                if self.session is not None:
                    self.session.cancel_event.set()
                    self.session.preview_stop.set()
            if self.recorder.recording:
                try:
                    self.recorder.stop()
                except Exception:
                    pass
            if self.listener is not None:
                self.listener.stop()
            self.timer.stop()
            rumps.quit_application()

    return VoiceInputApp


class MacOSRightOptionListener:
    """Native global/local monitor for start, cancel, and safe confirmation."""

    def __init__(self, app):
        self.app = app
        self.right_option_down = False
        self.monitors = []
        self.handlers = []
        self.state_lock = threading.RLock()
        self.event_tap = None
        self.event_tap_source = None
        self.event_tap_callback = None

    def start(self) -> None:
        from AppKit import (
            NSEvent,
            NSEventMaskFlagsChanged,
            NSEventModifierFlagOption,
        )

        def process_flags(event, callback) -> None:
            try:
                option_down = bool(
                    int(event.modifierFlags()) & int(NSEventModifierFlagOption)
                )
                with self.state_lock:
                    should_fire, self.right_option_down = right_option_transition(
                        int(event.keyCode()),
                        option_down,
                        self.right_option_down,
                    )
                if should_fire:
                    callback()
            except Exception:
                traceback.print_exc()

        def global_flags_changed(event) -> None:
            process_flags(event, lambda: run_on_main(self.app.on_hotkey))

        def local_flags_changed(event):
            process_flags(event, self.app.on_hotkey)
            return event

        self.handlers = [
            global_flags_changed,
            local_flags_changed,
        ]
        self.monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskFlagsChanged,
                global_flags_changed,
            ),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSEventMaskFlagsChanged,
                local_flags_changed,
            ),
        ]
        if any(monitor is None for monitor in self.monitors):
            self.stop()
            raise RuntimeError("无法创建 macOS 全局按键监听")
        escape_ready = start_optional_escape_monitor(self._start_key_event_tap)
        print(
            "[启动] macOS 原生右 Option 已就绪，Esc "
            + ("已就绪" if escape_ready else "等待辅助功能授权"),
            flush=True,
        )

    def _start_key_event_tap(self) -> None:
        from Quartz import (
            CFMachPortCreateRunLoopSource,
            CFRunLoopAddSource,
            CFRunLoopGetMain,
            CGEventGetFlags,
            CGEventGetIntegerValueField,
            CGEventMaskBit,
            CGEventTapCreate,
            CGEventTapEnable,
            kCFRunLoopCommonModes,
            kCGEventKeyDown,
            kCGEventTapDisabledByTimeout,
            kCGEventTapDisabledByUserInput,
            kCGEventTapOptionDefault,
            kCGHeadInsertEventTap,
            kCGKeyboardEventKeycode,
            kCGSessionEventTap,
        )

        def event_callback(_proxy, event_type, event, _refcon):
            try:
                if event_type in (
                    kCGEventTapDisabledByTimeout,
                    kCGEventTapDisabledByUserInput,
                ):
                    if self.event_tap is not None:
                        CGEventTapEnable(self.event_tap, True)
                    return event
                if event_type != kCGEventKeyDown:
                    return event
                key_code = int(
                    CGEventGetIntegerValueField(
                        event,
                        kCGKeyboardEventKeycode,
                    )
                )
                if key_code == ESCAPE_KEY_CODE and self.app.on_escape():
                    return None
            except Exception:
                traceback.print_exc()
            return event

        self.event_tap_callback = event_callback
        self.event_tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventKeyDown),
            event_callback,
            None,
        )
        if self.event_tap is None:
            raise RuntimeError("无法创建 Esc 拦截器，请检查辅助功能权限")
        self.event_tap_source = CFMachPortCreateRunLoopSource(
            None,
            self.event_tap,
            0,
        )
        CFRunLoopAddSource(
            CFRunLoopGetMain(),
            self.event_tap_source,
            kCFRunLoopCommonModes,
        )
        CGEventTapEnable(self.event_tap, True)

    def stop(self) -> None:
        from AppKit import NSEvent
        from Quartz import (
            CFMachPortInvalidate,
            CFRunLoopGetMain,
            CFRunLoopRemoveSource,
            kCFRunLoopCommonModes,
        )

        for monitor in self.monitors:
            if monitor is not None:
                NSEvent.removeMonitor_(monitor)
        if self.event_tap_source is not None:
            CFRunLoopRemoveSource(
                CFRunLoopGetMain(),
                self.event_tap_source,
                kCFRunLoopCommonModes,
            )
        if self.event_tap is not None:
            CFMachPortInvalidate(self.event_tap)
        self.event_tap_source = None
        self.event_tap = None
        self.event_tap_callback = None
        self.monitors = []
        self.handlers = []
        self.right_option_down = False


def create_listener(app, hotkey: str):
    if hotkey.strip().lower() == "<alt_r>":
        listener = MacOSRightOptionListener(app)
        listener.start()
        return listener

    from pynput import keyboard

    if "+" in hotkey:
        listener = keyboard.GlobalHotKeys(
            {
                hotkey: app.on_hotkey,
                "<esc>": app.on_escape,
            }
        )
    else:
        key_name = hotkey.strip("<>")
        target_key = getattr(keyboard.Key, key_name, None)
        if target_key is None:
            raise ValueError(f"无法识别的热键: {hotkey}")

        def on_press(key):
            try:
                if key == target_key:
                    app.on_hotkey()
                elif key == keyboard.Key.esc:
                    app.on_escape()
            except Exception:
                traceback.print_exc()

        listener = keyboard.Listener(on_press=on_press)
    listener.start()
    return listener


def acquire_single_instance_lock():
    handle = LOCK_PATH.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def validate_config(config: dict) -> list[str]:
    missing = []
    if not configured_api_key(config.get("asr", {})):
        missing.append("ASR API Key")
    if not configured_api_key(config.get("llm", {})):
        missing.append("LLM API Key")
    return missing


def run_audio_smoke_test(
    open_stream: bool = False,
    *,
    sounddevice_module=None,
    soundfile_module=None,
    numpy_module=None,
) -> int:
    """Verify bundled audio libraries, optionally opening the real input."""
    wav_path = None
    try:
        if numpy_module is None:
            import numpy as numpy_module
        if sounddevice_module is None:
            import sounddevice as sounddevice_module
        if soundfile_module is None:
            import soundfile as soundfile_module

        device = sounddevice_module.query_devices(kind="input")
        device_name = str(device.get("name", "默认输入设备"))
        sample_rate = int(round(float(device["default_samplerate"])))
        if int(device.get("max_input_channels", 0)) < 1:
            raise RuntimeError("默认音频设备没有输入通道")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix="voice_input_audio_smoke_",
            suffix=".wav",
        )
        os.close(descriptor)
        wav_path = Path(temporary_name)
        expected_frames = 320
        soundfile_module.write(
            str(wav_path),
            numpy_module.zeros((expected_frames, 1), dtype="float32"),
            16_000,
        )
        written_audio, written_rate = soundfile_module.read(
            str(wav_path),
            dtype="float32",
            always_2d=True,
        )
        if int(written_rate) != 16_000 or len(written_audio) != expected_frames:
            raise RuntimeError("WAV 写入后校验失败")

        if open_stream:
            stream = sounddevice_module.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
            )
            try:
                stream.start()
                time.sleep(0.12)
                stream.stop()
            finally:
                stream.close()
        print(
            f"AUDIO_SMOKE_OK device={device_name} "
            f"rate={sample_rate} wav=yes "
            f"stream={'yes' if open_stream else 'no'}",
            flush=True,
        )
        return 0
    except Exception as error:
        print(f"AUDIO_SMOKE_FAILED {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if wav_path is not None:
            wav_path.unlink(missing_ok=True)


def run_pipeline_smoke_test(wav_path: Path) -> int:
    """Exercise ASR and LLM using the installed configuration."""
    try:
        if not wav_path.is_file():
            raise FileNotFoundError(f"测试音频不存在：{wav_path}")
        pipeline = SpeechPipeline(CONFIG_STORE.load())
        raw = pipeline.transcribe(wav_path)
        if not raw:
            raise RuntimeError("语音识别返回空文本")
        result = pipeline.polish(raw, "中文")
        if not result:
            raise RuntimeError("文字整理返回空文本")
        print(
            f"PIPELINE_SMOKE_OK raw_chars={len(raw)} "
            f"result_chars={len(result)}",
            flush=True,
        )
        return 0
    except Exception as error:
        print(f"PIPELINE_SMOKE_FAILED {error}", file=sys.stderr, flush=True)
        return 1


def run_panel_preview(config: dict) -> None:
    import rumps

    app = rumps.App("🎙", quit_button="退出预览")
    selected = [config.get("output", {}).get("mode", "auto")]

    def change_mode(index: int):
        selected[0] = ResultPanel.MODES[index]
        panel.set_mode(selected[0])
        panel.update(status=f"输出语言：{selected[0]}")

    def copy_preview():
        copy_text(panel.latest_result)
        panel.update(status="预览结果已复制")

    panel = ResultPanel(
        config,
        change_mode,
        copy_preview,
        confirm_callback=lambda: panel.update(status="预览确认"),
    )
    panel.update(
        status="正在整理为 English…",
        raw="嗯，这个 microphone 的声音有点差，而且连接经常断。",
        result=(
            "This microphone has poor sound quality, and the connection "
            "keeps dropping."
        ),
    )

    def show_after_launch(timer):
        timer.stop()
        panel.show()

    preview_timer = rumps.Timer(show_after_launch, 0.2)
    preview_timer.start()
    app.run()


def run_settings_preview() -> None:
    import rumps

    app = rumps.App("🎙", quit_button="退出预览")
    controller = SettingsController(CONFIG_STORE)

    def show_after_launch(timer):
        timer.stop()
        controller.show()

    preview_timer = rumps.Timer(show_after_launch, 0.2)
    preview_timer.start()
    app.run()


def main() -> int:
    if "--pipeline-smoke-test" in sys.argv:
        option_index = sys.argv.index("--pipeline-smoke-test")
        if option_index + 1 >= len(sys.argv):
            print(
                "PIPELINE_SMOKE_FAILED 缺少 WAV 文件路径",
                file=sys.stderr,
                flush=True,
            )
            return 2
        return run_pipeline_smoke_test(Path(sys.argv[option_index + 1]))
    if "--recording-smoke-test" in sys.argv:
        return run_audio_smoke_test(open_stream=True)
    if "--audio-smoke-test" in sys.argv:
        return run_audio_smoke_test()

    config = CONFIG_STORE.load()
    if "--panel-preview" in sys.argv:
        run_panel_preview(config)
        return 0
    if "--settings-preview" in sys.argv:
        run_settings_preview()
        return 0

    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        print("[启动] 语音输入已经在运行", flush=True)
        return 2

    missing = validate_config(config)
    if missing:
        print("[配置] 缺少 " + "、".join(missing), flush=True)

    runtime_config = deepcopy(config)
    if os.environ.get("VOICE_INPUT_HOTKEY"):
        runtime_config["hotkey"] = os.environ["VOICE_INPUT_HOTKEY"]

    app_class = make_app()
    app = app_class(runtime_config)
    if (
        missing
        or not bool(config.get("onboarding", {}).get("completed", False))
        or not accessibility_is_trusted()
    ):
        app.schedule_settings()
    hotkey = str(runtime_config.get("hotkey", "<alt_r>"))
    listener = create_listener(app, hotkey)
    app.set_listener(listener)
    output = config.get("output", {})
    print(
        f"[启动] {humanize_hotkey(hotkey)} 开始/结束，完成后自动写入，Esc 取消",
        flush=True,
    )
    print(
        f"[启动] 输出语言 {output.get('mode', 'auto')}，"
        f"fallback={output.get('fallback', '中文')}",
        flush=True,
    )
    print(
        f"[启动] 辅助功能权限 {'已就绪' if accessibility_is_trusted() else '未授权'}",
        flush=True,
    )
    print(
        f"[启动] HTTPS 证书 {'已就绪' if SSL_CERT_PATH else '未找到'}",
        flush=True,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
