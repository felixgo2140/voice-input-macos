"""py2app build definition."""

from setuptools import setup


APP = ["voice_input.py"]
OPTIONS = {
    "arch": "arm64",
    "argv_emulation": False,
    "packages": [
        "rumps",
        "pynput",
        "sounddevice",
        "_sounddevice_data",
        "soundfile",
        "_soundfile_data",
        "numpy",
        "openai",
        "pyperclip",
        "certifi",
    ],
    "includes": [
        "ApplicationServices",
        "AppKit",
        "AVFoundation",
        "Foundation",
        "Quartz",
        "PyObjCTools",
        "credential_store",
        "macos_context",
        "settings_window",
        "voice_input_core",
    ],
    "plist": {
        "CFBundleDisplayName": "Voice Input",
        "CFBundleIdentifier": "com.felix.voiceinput",
        "CFBundleShortVersionString": "1.3.7",
        "CFBundleVersion": "1307",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "LSArchitecturePriority": ["arm64"],
        "LSRequiresNativeExecution": True,
        "NSMicrophoneUsageDescription": (
            "Voice Input uses the microphone only while you are dictating."
        ),
    },
}


setup(
    name="Voice Input",
    version="1.3.7",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
