"""Core services and pure helpers for the macOS voice input app."""

from __future__ import annotations

import base64
import json
import hashlib
import os
import queue
import re
import ssl
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


DEFAULT_CONFIG = {
    "hotkey": "<alt_r>",
    "asr": {
        "provider": "Qwen 百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "realtime_url": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        "realtime_model": "paraformer-realtime-v2",
        "api_key": "",
        "api_key_env": "VOICE_INPUT_ASR_API_KEY",
        "keychain_account": "qwen-bailian-api-key",
        "model": "qwen3-asr-flash",
        "max_file_seconds": 300,
        "chunk_seconds": 270,
    },
    "llm": {
        "provider": "Qwen 百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "api_key_env": "VOICE_INPUT_LLM_API_KEY",
        "keychain_account": "qwen-bailian-api-key",
        "model": "qwen-plus",
        "stream": True,
        "temperature": 0.2,
    },
    "output": {"mode": "auto", "fallback": "中文"},
    "ui": {
        "follow_caret": True,
        "panel_width": 340,
        "panel_height": 170,
        "caret_gap": 52,
    },
    "realtime_preview": {
        "enabled": True,
        "first_update_seconds": 1.2,
        "interval_seconds": 2.0,
        "minimum_seconds": 0.8,
        "max_consecutive_failures": 2,
    },
    "recording": {"max_seconds": 600},
    "network": {"timeout_seconds": 45, "max_retries": 1},
    "restore_clipboard": True,
    "auto_paste": True,
    "onboarding": {"completed": False},
}


PROVIDER_CREDENTIAL_ALIASES = {
    "Qwen": "qwen-bailian",
    "Qwen 百炼": "qwen-bailian",
    "Qwen 3.8 Coding Plan": "qwen-coding",
    "Kimi": "kimi",
    "Kimi Coding Plan": "kimi-coding",
    "OpenAI": "openai",
    "智谱 GLM-ASR": "zhipu",
    "DeepSeek": "deepseek",
    "Groq Whisper": "groq",
    "Groq": "groq",
}


def credential_account_for_provider(provider: str) -> str:
    """Return one shared private credential account per API provider."""
    provider = str(provider or "").strip()
    alias = PROVIDER_CREDENTIAL_ALIASES.get(provider)
    if not alias:
        alias = re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-")
    if not alias:
        digest = hashlib.sha256(provider.encode("utf-8")).hexdigest()[:10]
        alias = f"provider-{digest}"
    return f"{alias}-api-key"

POLISH_SYSTEM_PROMPT = """你是一个语音文字整理助手。用户会提供一段语音转写原文，通常是中文，可能夹杂英文词汇。请在不改变原意的前提下，将它整理成清晰、简洁、自然的文字，并用{target}输出。

要求：
1. 删除“嗯、啊、那个、就是、然后”等语气词、口头禅、卡顿和无意义重复。
2. 保留事实、语气、专有名词、数字和关键细节；不要回答原文中的问题，不要补充观点。
3. 整段统一使用{target}。若目标是 English，译成自然、地道、简洁的英文；若目标是中文，英文品牌名或术语可按语境保留。
4. 根据内容合理添加标点和分段，保持消息、评论、邮件等原本用途的语气。
5. 只输出最终正文，不要解释、标题、前缀、Markdown 围栏或包裹全文的引号。"""


def deep_fill_missing(
    target: MutableMapping, defaults: Mapping
) -> bool:
    """Recursively add defaults without overwriting user choices."""
    changed = False
    for key, default_value in defaults.items():
        if key not in target:
            target[key] = deepcopy(default_value)
            changed = True
            continue
        current = target[key]
        if isinstance(current, MutableMapping) and isinstance(
            default_value, Mapping
        ):
            changed = deep_fill_missing(current, default_value) or changed
    return changed


def deep_update(target: MutableMapping, patch: Mapping) -> None:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(
            target.get(key), MutableMapping
        ):
            deep_update(target[key], value)
        else:
            target[key] = deepcopy(value)


class ConfigStore:
    """Load configuration and keep credentials in a private local file."""

    def __init__(self, path: Path | str, secret_store=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._secret_store = secret_store

    @property
    def secret_store(self):
        if self._secret_store is None:
            from credential_store import get_secret_store

            self._secret_store = get_secret_store()
        return self._secret_store

    def load(self) -> dict:
        with self._lock:
            if self.path.exists():
                with self.path.open(encoding="utf-8") as handle:
                    config = json.load(handle)
                if not isinstance(config, dict):
                    raise ValueError("配置文件必须是 JSON 对象")
            else:
                config = deepcopy(DEFAULT_CONFIG)

            changed = deep_fill_missing(config, DEFAULT_CONFIG)
            changed = self._migrate_plaintext_secrets(config) or changed
            changed = self._migrate_provider_secret_accounts(config) or changed
            if changed or not self.path.exists():
                self._write(config)
            return config

    def update(self, patch: Mapping) -> dict:
        with self._lock:
            config = self.load()
            deep_update(config, patch)
            self._write(config)
            return config

    def set_output_mode(self, mode: str) -> dict:
        return self.update({"output": {"mode": normalize_output_mode(mode)}})

    def save_credentials(
        self,
        patch: Mapping,
        *,
        asr_secret: str | None = None,
        llm_secret: str | None = None,
    ) -> dict:
        """Save non-secret settings and optional credentials atomically enough.

        Credential writes happen first. The config file never receives the secret.
        Blank secret fields mean "keep the existing credential".
        """
        with self._lock:
            config = self.load()
            deep_update(config, patch)
            pending_secrets = {}
            for section_name, secret in (
                ("asr", asr_secret),
                ("llm", llm_secret),
            ):
                if secret is None or not secret.strip():
                    continue
                account = str(
                    config[section_name].get(
                        "keychain_account", f"{section_name}-api-key"
                    )
                )
                cleaned = secret.strip()
                if account in pending_secrets and pending_secrets[account] != cleaned:
                    raise ValueError("同一服务商的 API Key 必须保持一致")
                pending_secrets[account] = cleaned
                config[section_name]["api_key"] = ""
            for account, secret in pending_secrets.items():
                self.secret_store.set(account, secret)
            self._write(config)
            return config

    def secret_for(self, section_name: str, config: Mapping | None = None) -> str:
        config = config or self.load()
        return configured_api_key(
            config.get(section_name, {}),
            secret_store=self.secret_store,
        )

    def _migrate_plaintext_secrets(self, config: MutableMapping) -> bool:
        changed = False
        for section_name in ("asr", "llm"):
            section = config.get(section_name)
            if not isinstance(section, MutableMapping):
                continue
            legacy = str(section.get("api_key", "") or "").strip()
            if not legacy:
                continue
            account = str(
                section.get("keychain_account", f"{section_name}-api-key")
            )
            try:
                self.secret_store.set(account, legacy)
            except Exception:
                # Preserve the old credential if private storage is unavailable.
                continue
            section["api_key"] = ""
            changed = True
        return changed

    def _migrate_provider_secret_accounts(
        self, config: MutableMapping
    ) -> bool:
        """Move legacy ASR/LLM slots into provider-specific credential slots."""
        changed = False
        for section_name in ("asr", "llm"):
            section = config.get(section_name)
            if not isinstance(section, MutableMapping):
                continue
            legacy_account = f"{section_name}-api-key"
            current_account = str(
                section.get("keychain_account", legacy_account) or ""
            ).strip()
            if current_account != legacy_account:
                continue
            provider = str(section.get("provider", "") or "").strip()
            provider_account = credential_account_for_provider(provider)
            try:
                legacy_secret = self.secret_store.get(legacy_account).strip()
                provider_secret = self.secret_store.get(provider_account).strip()
                if legacy_secret and not provider_secret:
                    self.secret_store.set(provider_account, legacy_secret)
            except Exception:
                continue
            section["keychain_account"] = provider_account
            changed = True
        return changed

    def _write(self, config: Mapping) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except Exception:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


def normalize_output_mode(mode: str) -> str:
    aliases = {
        "auto": "auto",
        "自动": "auto",
        "自动检测": "auto",
        "zh": "中文",
        "中文": "中文",
        "chinese": "中文",
        "en": "English",
        "english": "English",
        "英文": "English",
    }
    normalized = aliases.get(str(mode).strip().lower())
    if normalized is None:
        raise ValueError(f"不支持的输出语言模式：{mode}")
    return normalized


def detect_language_from_texts(texts: Iterable[object]) -> str | None:
    """Return ``zh`` or ``en`` using lightweight script scoring."""
    text = " ".join(str(item) for item in texts if item is not None)
    if not text.strip():
        return None
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    latin = sum(
        ("a" <= char.lower() <= "z") for char in text
    )
    if cjk >= 2 and cjk * 3 >= latin:
        return "zh"
    if latin >= 2:
        return "en"
    if cjk:
        return "zh"
    return None


def resolve_output_language(
    output_config: Mapping, detected_language: str | None
) -> str:
    mode = normalize_output_mode(str(output_config.get("mode", "auto")))
    if mode != "auto":
        return mode
    if detected_language == "en":
        return "English"
    if detected_language == "zh":
        return "中文"
    fallback = normalize_output_mode(
        str(output_config.get("fallback", "中文"))
    )
    return "中文" if fallback == "auto" else fallback


def sanitize_model_output(text: str | None) -> str:
    """Remove common model wrappers while preserving the body."""
    result = (text or "").strip()
    if result.startswith("```") and result.endswith("```"):
        result = re.sub(
            r"^```(?:text|markdown)?\s*", "", result, flags=re.I
        )
        result = re.sub(r"\s*```$", "", result)
    for prefix in ("整理后：", "整理结果：", "输出：", "Result:", "Output:"):
        if result.lower().startswith(prefix.lower()):
            result = result[len(prefix) :].lstrip()
            break
    for left, right in (('"', '"'), ("“", "”"), ("'", "'")):
        if (
            result.startswith(left)
            and result.endswith(right)
            and result.count("\n") <= 1
        ):
            result = result[len(left) : -len(right)].strip()
            break
    return result


def is_meaningful_transcript(text: str | None) -> bool:
    """Reject stream markers and punctuation/noise-only transcripts."""
    compact = re.sub(r"[\s\W_#]+", "", text or "", flags=re.UNICODE)
    return bool(compact)


def join_transcript_parts(parts: Iterable[str | None]) -> str:
    """Join independently transcribed chunks without breaking English."""
    result = ""
    for value in parts:
        part = (value or "").strip()
        if not part:
            continue
        separator = ""
        if (
            result
            and result[-1].isascii()
            and result[-1].isalnum()
            and part[0].isascii()
            and part[0].isalnum()
        ):
            separator = " "
        result += separator + part
    return result


@dataclass(frozen=True)
class ScreenBounds:
    x: float
    y: float
    width: float
    height: float

    @property
    def max_x(self) -> float:
        return self.x + self.width

    @property
    def max_y(self) -> float:
        return self.y + self.height

    def contains(self, point_x: float, point_y: float) -> bool:
        return self.x <= point_x < self.max_x and self.y <= point_y < self.max_y


def panel_origin_for_caret(
    caret_frame: Sequence[float],
    panel_size: Sequence[float],
    screens: Sequence[ScreenBounds],
    coordinate_top: float,
    gap: float = 8,
) -> tuple[float, float]:
    """Convert AX top-left coordinates and keep the panel on-screen."""
    caret_x, caret_y, caret_width, caret_height = map(float, caret_frame)
    panel_width, panel_height = map(float, panel_size)
    appkit_caret_y = coordinate_top - caret_y - caret_height
    screen = next(
        (
            candidate
            for candidate in screens
            if candidate.contains(caret_x, appkit_caret_y)
        ),
        screens[0] if screens else ScreenBounds(0, 0, 1440, 900),
    )
    x = caret_x
    y = appkit_caret_y - panel_height - gap
    if y < screen.y:
        y = appkit_caret_y + caret_height + gap
    x = min(max(x, screen.x), max(screen.x, screen.max_x - panel_width))
    y = min(max(y, screen.y), max(screen.y, screen.max_y - panel_height))
    return x, y


def configured_api_key(section: Mapping, secret_store=None) -> str:
    env_name = str(section.get("api_key_env", "") or "").strip()
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    account = str(section.get("keychain_account", "") or "").strip()
    if account:
        try:
            if secret_store is None:
                from credential_store import get_secret_store

                secret_store = get_secret_store()
            value = secret_store.get(account).strip()
            if value:
                return value
        except Exception:
            pass
    return str(section.get("api_key", "") or "").strip()


def qwen_realtime_websocket_url(asr_config: Mapping) -> str:
    """Build the matching Qwen realtime endpoint from the saved HTTP one."""
    configured = str(asr_config.get("realtime_url", "") or "").strip()
    source = configured or str(asr_config.get("base_url", "") or "").strip()
    parsed = urlparse(source)
    if not parsed.hostname:
        raise ValueError("Qwen 实时识别地址无效")
    scheme = "wss" if parsed.scheme in {"http", "https", "ws", "wss"} else "wss"
    model = str(
        asr_config.get("realtime_model", "qwen3-asr-flash-realtime")
        or "qwen3-asr-flash-realtime"
    ).strip()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["model"] = model
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            "/api-ws/v1/realtime",
            "",
            urlencode(query),
            "",
        )
    )


def float_audio_to_pcm16(audio, source_rate: int, target_rate: int = 16_000) -> bytes:
    """Convert a mono float callback frame to little-endian PCM16."""
    import numpy as np

    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        return b""
    source_rate = int(source_rate)
    target_rate = int(target_rate)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("音频采样率必须大于 0")
    if source_rate != target_rate:
        ratio = source_rate / float(target_rate)
        if ratio.is_integer():
            samples = samples[:: int(ratio)]
        else:
            output_size = max(
                1,
                int(round(samples.size * target_rate / float(source_rate))),
            )
            source_positions = np.arange(samples.size, dtype=np.float64)
            target_positions = np.linspace(
                0,
                max(0, samples.size - 1),
                output_size,
                dtype=np.float64,
            )
            samples = np.interp(target_positions, source_positions, samples)
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


class QwenRealtimeTranscriber:
    """Stream microphone frames to Qwen while recording is still active."""

    def __init__(
        self,
        asr_config: Mapping,
        on_partial: Callable[[str], None] | None = None,
        connect_timeout: float = 8.0,
    ):
        self.asr_config = asr_config
        self.api_key = configured_api_key(asr_config)
        if not self.api_key:
            raise ValueError("尚未配置语音识别 API Key")
        self.url = qwen_realtime_websocket_url(asr_config)
        self.on_partial = on_partial
        self.connect_timeout = max(1.0, float(connect_timeout))
        self.audio_queue: queue.Queue = queue.Queue(maxsize=1024)
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.final_received = threading.Event()
        self.sender_finished = threading.Event()
        self.accepting_audio = True
        self.latest_text = ""
        self.final_text = ""
        self.error: Exception | None = None
        self.ws = None
        self.socket_thread: threading.Thread | None = None
        self.sender_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()

    @staticmethod
    def supports(asr_config: Mapping) -> bool:
        provider = str(asr_config.get("provider", "") or "").lower()
        model = str(asr_config.get("model", "") or "").lower()
        return "qwen" in provider or model.startswith("qwen3-asr-")

    def start(self) -> None:
        if self.socket_thread is not None:
            return
        self.socket_thread = threading.Thread(
            target=self._run_socket,
            name="voice-input-qwen-realtime",
            daemon=True,
        )
        self.sender_thread = threading.Thread(
            target=self._send_audio,
            name="voice-input-qwen-audio",
            daemon=True,
        )
        self.socket_thread.start()
        self.sender_thread.start()

    def feed_audio(self, audio, sample_rate: int) -> None:
        if not self.accepting_audio or self.error is not None:
            return
        try:
            self.audio_queue.put_nowait((audio, int(sample_rate)))
        except queue.Full:
            self._set_error(RuntimeError("实时识别发送队列已满"))
            self.accepting_audio = False

    def finish(self, timeout: float = 6.0) -> str:
        """Drain captured frames, commit once, and return the final transcript."""
        self.accepting_audio = False
        self._enqueue_end()
        deadline = time.monotonic() + max(0.5, float(timeout))
        self.final_received.wait(max(0.0, deadline - time.monotonic()))
        if self.final_received.is_set():
            self.finished.wait(min(0.5, max(0.0, deadline - time.monotonic())))
        result = self.final_text.strip()
        if not result and is_meaningful_transcript(self.latest_text):
            # The server's last text event already contains the complete
            # rolling transcript. It is a safe low-latency fallback if the
            # separate completed event is delayed or lost during close.
            result = self.latest_text.strip()
        if not result and self.error is None and time.monotonic() >= deadline:
            self._set_error(TimeoutError("实时识别收尾超时"))
        if not self.finished.is_set():
            self._close_socket()
        return result if is_meaningful_transcript(result) else ""

    def abort(self) -> None:
        self.accepting_audio = False
        self._enqueue_end()
        self._close_socket()
        self.finished.set()

    def _enqueue_end(self) -> None:
        try:
            self.audio_queue.put_nowait(None)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.audio_queue.put_nowait(None)
            except queue.Full:
                pass

    def _run_socket(self) -> None:
        try:
            import certifi
            import websocket

            self.ws = websocket.WebSocketApp(
                self.url,
                header=self._headers(),
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self.ws.run_forever(
                sslopt={
                    "ca_certs": certifi.where(),
                    "cert_reqs": ssl.CERT_REQUIRED,
                }
            )
        except Exception as error:
            self._set_error(error)
        finally:
            self.finished.set()
            self.ready.set()

    def _headers(self) -> list[str]:
        return [
            f"Authorization: Bearer {self.api_key}",
            "OpenAI-Beta: realtime=v1",
        ]

    def _on_open(self, ws) -> None:
        self._send_json(
            {
                "event_id": f"event_config_{time.time_ns()}",
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": 16_000,
                    "turn_detection": None,
                },
            }
        )

    def _on_message(self, _ws, message: str) -> None:
        try:
            event = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        event_type = str(event.get("type", ""))
        if event_type == "session.updated":
            self.ready.set()
            return
        if event_type == "conversation.item.input_audio_transcription.text":
            partial = f"{event.get('text', '')}{event.get('stash', '')}".strip()
            if is_meaningful_transcript(partial):
                self.latest_text = partial
                if self.on_partial:
                    self.on_partial(partial)
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript", "") or "").strip()
            if is_meaningful_transcript(transcript):
                self.final_text = transcript
                self.latest_text = transcript
                if self.on_partial:
                    self.on_partial(transcript)
            self.final_received.set()
            return
        if event_type == "session.finished":
            self.finished.set()
            self._close_socket()
            return
        if event_type == "error":
            details = event.get("error") or event.get("message") or event
            self._set_error(RuntimeError(f"Qwen 实时识别失败：{details}"))
            self.final_received.set()
            self._close_socket()

    def _on_error(self, _ws, error) -> None:
        if not self.finished.is_set():
            self._set_error(
                error if isinstance(error, Exception) else RuntimeError(str(error))
            )
            self.final_received.set()

    def _on_close(self, _ws, _status_code, _message) -> None:
        if (
            not self.final_received.is_set()
            and self.accepting_audio
            and self.error is None
        ):
            self._set_error(RuntimeError("实时识别连接意外关闭"))
        self.final_received.set()
        self.finished.set()
        self.ready.set()

    def _send_audio(self) -> None:
        if not self.ready.wait(self.connect_timeout):
            self._set_error(TimeoutError("实时识别连接超时"))
            self.sender_finished.set()
            return
        if self.error is not None or self.finished.is_set():
            self.sender_finished.set()
            return
        try:
            while True:
                item = self.audio_queue.get()
                if item is None:
                    break
                audio, sample_rate = item
                pcm = float_audio_to_pcm16(audio, sample_rate)
                if not pcm:
                    continue
                self._send_json(
                    {
                        "event_id": f"event_audio_{time.time_ns()}",
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm).decode("ascii"),
                    }
                )
            if self.error is None and not self.finished.is_set():
                self._send_json(
                    {
                        "event_id": f"event_commit_{time.time_ns()}",
                        "type": "input_audio_buffer.commit",
                    }
                )
                self._send_json(
                    {
                        "event_id": f"event_finish_{time.time_ns()}",
                        "type": "session.finish",
                    }
                )
        except Exception as error:
            self._set_error(error)
            self.final_received.set()
            self._close_socket()
        finally:
            self.sender_finished.set()

    def _send_json(self, payload: Mapping) -> None:
        ws = self.ws
        if ws is None or ws.sock is None or not ws.sock.connected:
            raise RuntimeError("实时识别连接已断开")
        ws.send(json.dumps(payload, ensure_ascii=False))

    def _set_error(self, error: Exception) -> None:
        with self._state_lock:
            if self.error is None:
                self.error = error
        self.ready.set()

    def _close_socket(self) -> None:
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


class DashScopeRealtimeTranscriber(QwenRealtimeTranscriber):
    """Low-latency DashScope duplex ASR used for the five-times fast path."""

    def __init__(
        self,
        asr_config: Mapping,
        on_partial: Callable[[str], None] | None = None,
        connect_timeout: float = 8.0,
    ):
        super().__init__(asr_config, on_partial, connect_timeout)
        self.model = str(
            asr_config.get("realtime_model", "paraformer-realtime-v2")
            or "paraformer-realtime-v2"
        ).strip()
        parsed = urlparse(
            str(asr_config.get("realtime_url", "") or "").strip()
            or str(asr_config.get("base_url", "") or "").strip()
        )
        if not parsed.hostname:
            raise ValueError("DashScope 实时识别地址无效")
        self.url = urlunparse(
            ("wss", parsed.netloc, "/api-ws/v1/inference", "", "", "")
        )
        self.task_id = uuid.uuid4().hex
        self.completed_parts: list[str] = []
        self.current_partial = ""

    def finish(self, timeout: float = 3.0) -> str:
        self.accepting_audio = False
        self._enqueue_end()
        if self.final_text and not self.current_partial:
            # A short pause has already produced a sentence-end result while
            # the user was reaching for Option. Only flush the sender queue;
            # there is no reason to wait for the task-finished round trip.
            if self.sender_finished.wait(min(0.8, max(0.2, timeout))):
                self.final_received.wait(0.1)
                return self.final_text.strip()
        return super().finish(timeout)

    def _headers(self) -> list[str]:
        return [f"Authorization: Bearer {self.api_key}"]

    def _on_open(self, _ws) -> None:
        self._send_json(
            {
                "header": {
                    "action": "run-task",
                    "task_id": self.task_id,
                    "streaming": "duplex",
                },
                "payload": {
                    "task_group": "audio",
                    "task": "asr",
                    "function": "recognition",
                    "model": self.model,
                    "parameters": {
                        "format": "pcm",
                        "sample_rate": 16_000,
                        "semantic_punctuation_enabled": False,
                        "max_sentence_silence": 200,
                    },
                    "input": {},
                },
            }
        )

    def _on_message(self, _ws, message: str) -> None:
        try:
            event = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        header = event.get("header", {})
        event_type = str(header.get("event", ""))
        if event_type == "task-started":
            self.ready.set()
            return
        if event_type == "result-generated":
            sentence = (
                event.get("payload", {})
                .get("output", {})
                .get("sentence", {})
            )
            partial = str(sentence.get("text", "") or "").strip()
            if is_meaningful_transcript(partial):
                combined = join_transcript_parts(
                    [*self.completed_parts, partial]
                )
                self.current_partial = partial
                self.latest_text = combined
                if self.on_partial:
                    self.on_partial(combined)
                if bool(sentence.get("sentence_end", False)):
                    self.completed_parts.append(partial)
                    self.current_partial = ""
                    self.final_text = join_transcript_parts(
                        self.completed_parts
                    )
            return
        if event_type == "task-finished":
            if not self.final_text:
                self.final_text = self.latest_text
            self.final_received.set()
            self.finished.set()
            self._close_socket()
            return
        if event_type == "task-failed":
            message = header.get("error_message") or header.get("error_code")
            self._set_error(
                RuntimeError(f"DashScope 实时识别失败：{message or '未知错误'}")
            )
            self.final_received.set()
            self._close_socket()

    def _send_audio(self) -> None:
        if not self.ready.wait(self.connect_timeout):
            self._set_error(TimeoutError("实时识别连接超时"))
            self.sender_finished.set()
            return
        if self.error is not None or self.finished.is_set():
            self.sender_finished.set()
            return
        try:
            while True:
                item = self.audio_queue.get()
                if item is None:
                    break
                audio, sample_rate = item
                pcm = float_audio_to_pcm16(audio, sample_rate)
                if pcm:
                    self._send_binary(pcm)
            if self.error is None and not self.finished.is_set():
                self._send_json(
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": self.task_id,
                            "streaming": "duplex",
                        },
                        "payload": {"input": {}},
                    }
                )
        except Exception as error:
            self._set_error(error)
            self.final_received.set()
            self._close_socket()
        finally:
            self.sender_finished.set()

    def _send_binary(self, payload: bytes) -> None:
        import websocket

        ws = self.ws
        if ws is None or ws.sock is None or not ws.sock.connected:
            raise RuntimeError("实时识别连接已断开")
        ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)


def create_realtime_transcriber(
    asr_config: Mapping,
    on_partial: Callable[[str], None] | None = None,
    connect_timeout: float = 8.0,
) -> QwenRealtimeTranscriber:
    model = str(asr_config.get("realtime_model", "") or "").lower()
    transcriber_class = (
        DashScopeRealtimeTranscriber
        if model.startswith(("paraformer-", "qwen-audio-"))
        else QwenRealtimeTranscriber
    )
    return transcriber_class(asr_config, on_partial, connect_timeout)


class Recorder:
    """Thread-safe mono microphone recorder."""

    def __init__(self, samplerate: int = 16_000):
        self.samplerate = samplerate
        self.frames = []
        self.stream = None
        self.recording = False
        self.audio_listener: Callable[[object, int], None] | None = None
        self._lock = threading.RLock()

    def set_audio_listener(
        self,
        listener: Callable[[object, int], None] | None,
    ) -> None:
        with self._lock:
            self.audio_listener = listener

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self.recording:
                return
            self.frames = []
            stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            stream.start()
            self.stream = stream
            self.recording = True

    def _callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            print(f"[录音] {status}", flush=True)
        captured = None
        listener = None
        sample_rate = self.samplerate
        with self._lock:
            if self.recording:
                captured = indata.copy()
                self.frames.append(captured)
                listener = self.audio_listener
                sample_rate = self.samplerate
        if captured is not None and listener is not None:
            listener(captured, sample_rate)

    def stop(self, wav_path: Path | None = None) -> float:
        with self._lock:
            if not self.recording:
                return 0.0
            self.recording = False
            stream = self.stream
            self.stream = None
        if stream is not None:
            stream.stop()
            stream.close()
        data = self._copied_audio()
        if wav_path is not None:
            self._write_audio(data, wav_path)
        return len(data) / float(self.samplerate)

    def snapshot(self, wav_path: Path) -> float:
        with self._lock:
            if not self.recording:
                return 0.0
            data = self._copied_audio()
        self._write_audio(data, wav_path)
        return len(data) / float(self.samplerate)

    def _copied_audio(self):
        import numpy as np

        if not self.frames:
            return np.empty((0, 1), dtype="float32")
        return np.concatenate([frame.copy() for frame in self.frames], axis=0)

    def _write_audio(self, data, wav_path: Path) -> None:
        import soundfile as sf

        sf.write(str(wav_path), data, self.samplerate)


class SpeechPipeline:
    """ASR plus streamed LLM cleanup using compatible model endpoints."""

    def __init__(self, config: Mapping[str, object]):
        from openai import OpenAI

        self.config = config
        network = config.get("network", {})
        timeout = float(network.get("timeout_seconds", 45))
        max_retries = int(network.get("max_retries", 1))
        self.asr_config = config["asr"]
        self.llm_config = config["llm"]
        asr_key = configured_api_key(self.asr_config)
        llm_key = configured_api_key(self.llm_config)
        if not asr_key:
            raise ValueError("尚未配置语音识别 API Key")
        if not llm_key:
            raise ValueError("尚未配置文字整理 API Key")
        self.asr_client = OpenAI(
            api_key=asr_key,
            base_url=str(self.asr_config["base_url"]),
            timeout=timeout,
            max_retries=max_retries,
        )
        self.llm_client = OpenAI(
            api_key=llm_key,
            base_url=str(self.llm_config["base_url"]),
            timeout=timeout,
            max_retries=max_retries,
        )

    @staticmethod
    def _stream_text(event) -> str:
        if isinstance(event, str):
            return event
        if isinstance(event, Mapping):
            for key in ("delta", "text", "transcript"):
                value = event.get(key)
                if isinstance(value, str):
                    return value
        for attribute in ("delta", "text", "transcript"):
            value = getattr(event, attribute, None)
            if isinstance(value, str):
                return value
            nested = getattr(value, "text", None)
            if isinstance(nested, str):
                return nested
        return ""

    def transcribe(
        self,
        wav_path: Path,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        chunks = self._split_long_audio(wav_path)
        if not chunks:
            return self._transcribe_single(wav_path, on_partial)
        completed: list[str] = []
        try:
            print(f"[转写] 长录音自动分为 {len(chunks)} 段", flush=True)
            for chunk in chunks:
                def on_chunk_partial(partial: str) -> None:
                    if on_partial:
                        on_partial(join_transcript_parts([*completed, partial]))

                text = self._transcribe_single(
                    chunk, on_chunk_partial if on_partial else None
                )
                if text:
                    completed.append(text)
            result = join_transcript_parts(completed)
            if on_partial and result:
                on_partial(result)
            return result
        finally:
            for chunk in chunks:
                try:
                    chunk.unlink(missing_ok=True)
                except OSError:
                    pass

    def _split_long_audio(self, wav_path: Path) -> list[Path]:
        import soundfile as sf

        try:
            info = sf.info(str(wav_path))
        except (OSError, RuntimeError):
            return []
        if not info.samplerate:
            return []
        duration = info.frames / float(info.samplerate)
        max_seconds = max(
            0.05, float(self.asr_config.get("max_file_seconds", 29))
        )
        if duration <= max_seconds:
            return []
        chunk_seconds = min(
            max(0.05, float(self.asr_config.get("chunk_seconds", 28))),
            max_seconds,
        )
        frames_per_chunk = max(1, int(info.samplerate * chunk_seconds))
        chunks: list[Path] = []
        try:
            with sf.SoundFile(str(wav_path)) as source:
                while True:
                    audio = source.read(
                        frames_per_chunk,
                        dtype="float32",
                        always_2d=True,
                    )
                    if len(audio) == 0:
                        break
                    temporary = tempfile.NamedTemporaryFile(
                        suffix=".wav",
                        prefix="voice_input_chunk_",
                        delete=False,
                    )
                    chunk_path = Path(temporary.name)
                    temporary.close()
                    sf.write(str(chunk_path), audio, source.samplerate)
                    chunks.append(chunk_path)
            return chunks
        except Exception:
            for chunk in chunks:
                try:
                    chunk.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _transcribe_single(
        self,
        wav_path: Path,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        if self._uses_qwen_chat_asr():
            return self._transcribe_qwen_single(wav_path, on_partial)

        if on_partial:
            pieces = ""
            try:
                with wav_path.open("rb") as handle:
                    stream = self.asr_client.audio.transcriptions.create(
                        model=str(self.asr_config["model"]),
                        file=handle,
                        stream=True,
                    )
                    for event in stream:
                        text = self._stream_text(event)
                        event_type = (
                            event.get("type", "")
                            if isinstance(event, Mapping)
                            else getattr(event, "type", "")
                        )
                        if text in {"#", "##"} or str(event_type).endswith(
                            ".done"
                        ):
                            continue
                        if text.startswith(pieces):
                            pieces = text
                        else:
                            pieces += text
                        if is_meaningful_transcript(pieces):
                            on_partial(pieces)
                result = pieces.strip()
                if is_meaningful_transcript(result):
                    return result
            except Exception:
                print(
                    "[转写] 流式响应不可用，回退到普通响应",
                    flush=True,
                )
        with wav_path.open("rb") as handle:
            response = self.asr_client.audio.transcriptions.create(
                model=str(self.asr_config["model"]),
                file=handle,
            )
        result = str(getattr(response, "text", "") or "").strip()
        if on_partial and result:
            on_partial(result)
        return result if is_meaningful_transcript(result) else ""

    def _uses_qwen_chat_asr(self) -> bool:
        provider = str(self.asr_config.get("provider", "")).strip().lower()
        model = str(self.asr_config.get("model", "")).strip().lower()
        return provider == "qwen" or model.startswith("qwen3-asr-")

    def _qwen_asr_request(self, wav_path: Path) -> dict:
        encoded = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        return {
            "model": str(self.asr_config["model"]),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:audio/wav;base64,{encoded}"
                            },
                        }
                    ],
                }
            ],
            "extra_body": {"asr_options": {"enable_itn": True}},
        }

    def _transcribe_qwen_single(
        self,
        wav_path: Path,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        request = self._qwen_asr_request(wav_path)
        if on_partial:
            pieces = ""
            try:
                stream = self.asr_client.chat.completions.create(
                    **request,
                    stream=True,
                )
                for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    text = str(getattr(delta, "content", "") or "")
                    if not text:
                        continue
                    pieces += text
                    if is_meaningful_transcript(pieces):
                        on_partial(pieces)
                result = pieces.strip()
                if is_meaningful_transcript(result):
                    return result
            except Exception:
                print(
                    "[转写] Qwen 流式响应不可用，回退到普通响应",
                    flush=True,
                )

        response = self.asr_client.chat.completions.create(
            **request,
            stream=False,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        result = str(getattr(message, "content", "") or "").strip()
        if on_partial and result:
            on_partial(result)
        return result if is_meaningful_transcript(result) else ""

    def polish(
        self,
        raw_text: str,
        target_language: str,
        on_partial: Callable[[str], None] | None = None,
    ) -> str:
        request = {
            "model": str(self.llm_config["model"]),
            "messages": [
                {
                    "role": "system",
                    "content": POLISH_SYSTEM_PROMPT.format(
                        target=target_language
                    ),
                },
                {"role": "user", "content": raw_text},
            ],
            "temperature": float(
                self.llm_config.get("temperature", 0.2)
            ),
        }
        provider = str(self.llm_config.get("provider", "") or "").lower()
        if "qwen 百炼" in provider:
            # Qwen Plus can otherwise enter its slower reasoning path. Voice
            # cleanup is a direct transformation task, so explicitly disable
            # thinking to cut several seconds without changing the output.
            request["extra_body"] = {"enable_thinking": False}
        if bool(self.llm_config.get("stream", True)):
            pieces = ""
            try:
                stream = self.llm_client.chat.completions.create(
                    **request, stream=True
                )
                for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = choices[0].delta.content or ""
                    pieces += delta
                    partial = sanitize_model_output(pieces)
                    if on_partial and partial:
                        on_partial(partial)
                result = sanitize_model_output(pieces)
                if on_partial and result:
                    on_partial(result)
                return result
            except Exception:
                if pieces:
                    raise
                print(
                    "[整理] 流式请求不可用，回退到普通请求",
                    flush=True,
                )
        response = self.llm_client.chat.completions.create(**request)
        result = sanitize_model_output(
            response.choices[0].message.content or ""
        )
        if on_partial and result:
            on_partial(result)
        return result


def copy_text(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


def paste_text(text: str, restore_clipboard: bool = True) -> None:
    """Paste text and restore the old clipboard if it remains unchanged."""
    import pyperclip
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    previous = pyperclip.paste()
    pyperclip.copy(text)
    time.sleep(0.04)
    key_down = CGEventCreateKeyboardEvent(None, 9, True)
    key_up = CGEventCreateKeyboardEvent(None, 9, False)
    CGEventSetFlags(key_down, kCGEventFlagMaskCommand)
    CGEventSetFlags(key_up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, key_down)
    CGEventPost(kCGHIDEventTap, key_up)
    if restore_clipboard:
        time.sleep(0.18)
        if pyperclip.paste() == text:
            pyperclip.copy(previous)


def humanize_hotkey(hotkey: str) -> str:
    names = {
        "<alt_r>": "右 Option",
        "<alt_l>": "左 Option",
        "<f8>": "F8",
        "<f9>": "F9",
    }
    return names.get(
        hotkey,
        hotkey.replace("<", "").replace(">", "").replace("+", " + "),
    )
