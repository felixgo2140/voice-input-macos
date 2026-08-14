# Voice Input for macOS

一个可在任意输入框使用的 macOS 菜单栏语音输入工具：

1. 在目标输入框中按一次右 `Option` 开始录音。
2. 再按一次右 `Option` 结束。
3. 工具实时转写、用 LLM 整理，并自动回到原输入框粘贴结果。
4. 按 `Esc` 可随时取消。

也可以从菜单栏图标中点击“开始录音”与“停止录音并处理”。

它不会接管 `Enter` 或 `Shift + Enter`，因此不影响聊天软件原本的发送和换行快捷键。

## 功能

- ASR 与 LLM 服务商、模型使用联动下拉选择，服务地址自动配置
- 中文、English 或根据当前网页/输入框自动判断
- 录音时通过实时音频流显示转写，停止后不再重复上传整段录音
- 菜单提供独立的开始和停止按钮；单次录音默认最长 10 分钟
- 完成后自动回贴，不依赖浮窗是否处于 active 状态
- 浮窗跟随光标，并兼容多显示器坐标
- 音频设备切换后自动刷新并尝试设备默认采样率
- API Key 保存在仅当前 macOS 用户可读的私有凭据文件中
- 首次启动提供模型、行为和系统权限设置界面

## 系统要求

- macOS 13 或更高版本
- Apple Silicon Mac（M1/M2/M3/M4/M5）
- 麦克风权限
- 辅助功能权限（全局快捷键、返回原输入框和粘贴）
- 用户自行提供 ASR 与 LLM API Key

默认使用：

- ASR：Qwen 百炼 `paraformer-realtime-v2`（失败时自动回退到 `qwen3-asr-flash`）
- LLM：Qwen 百炼 `qwen-plus`

默认识别与整理共用一把 Qwen 百炼 Key。Qwen 3.8 Coding Plan 与普通百炼使用
不同的端点和 API Key，因此在设置中仍作为独立选项保存。
文字整理也可选择 `Kimi Coding Plan / k3`；程序会自动使用该模型要求的
`temperature=1`。Codex 登录使用的 OAuth 凭据不会被读取或复用。

## 安装发布版

1. 从 [Releases](../../releases) 下载 Apple Silicon 版 ZIP。
2. 解压后把 `Voice Input.app` 拖到“应用程序”。
3. 首次打开后选择模型服务和模型，并填写 API Key。
4. 按设置窗口提示授予“麦克风”和“辅助功能”权限。

当前公开构建使用 ad-hoc 签名，尚未经过 Apple 公证。首次启动若被 Gatekeeper 拦截，请在 Finder 中右键应用并选择“打开”。不要从不可信来源下载重新打包的版本。

## 从源码运行

```bash
git clone https://github.com/felixgo2140/voice-input-macos.git
cd voice-input-macos
./setup.sh
./run.sh
```

## 构建 `.app`

首版发布包面向 Apple Silicon，需要 Python 3.12：

```bash
./build-app.sh
./package-release.sh 1.4.1
```

如果拥有 Apple Developer ID：

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build-app.sh
```

公证需要额外执行 `xcrun notarytool submit` 和 `xcrun stapler staple`。仓库不包含证书或 Apple 凭据。

## 配置与数据

- 普通配置：`~/Library/Application Support/VoiceInput/config.json`
- API Key：`~/Library/Application Support/VoiceInput/credentials.json`
  （权限 `600`，仅当前 macOS 用户可读）
- 日志：由启动方式决定；应用不会主动记录输入框上下文

环境变量优先级高于本地凭据文件：

- `VOICE_INPUT_ASR_API_KEY`
- `VOICE_INPUT_LLM_API_KEY`
- `VOICE_INPUT_CONFIG_PATH`
- `VOICE_INPUT_CREDENTIALS_PATH`

高级用户可参考 [`config.example.json`](config.example.json)。不要提交自己的 `config.json`。

## 隐私

录音只在用户按下右 `Option` 后开始。音频会发送到用户配置的 ASR 服务，转写文本会发送到用户配置的 LLM 服务。应用不提供中转服务器，也不收集遥测。详见 [`PRIVACY.md`](PRIVACY.md)。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile \
  voice_input.py voice_input_core.py macos_context.py \
  credential_store.py settings_window.py setup_app.py
```

## License

[MIT](LICENSE)
