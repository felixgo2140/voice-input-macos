"""Small macOS Accessibility helpers used by the voice input app."""

from __future__ import annotations

from dataclasses import dataclass

from voice_input_core import detect_language_from_texts


@dataclass
class InputContext:
    element: object | None = None
    pid: int | None = None
    detected_language: str | None = None
    caret_frame: tuple[float, float, float, float] | None = None


def _copy_attribute(element, attribute):
    from ApplicationServices import AXUIElementCopyAttributeValue

    error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return None if error else value


def _element_pid(element) -> int | None:
    try:
        from ApplicationServices import AXUIElementGetPid

        error, pid = AXUIElementGetPid(element, None)
        return None if error else int(pid)
    except Exception:
        return None


def _ax_value(value, value_type):
    from ApplicationServices import AXValueGetValue

    ok, result = AXValueGetValue(value, value_type, None)
    return result if ok else None


def focused_element():
    from ApplicationServices import (
        AXUIElementCreateSystemWide,
        kAXFocusedUIElementAttribute,
    )

    system = AXUIElementCreateSystemWide()
    return _copy_attribute(system, kAXFocusedUIElementAttribute)


def collect_context_texts(element, ancestor_depth: int = 4) -> list[str]:
    """Collect short UI labels without logging or persisting their contents."""
    from ApplicationServices import (
        kAXDescriptionAttribute,
        kAXHelpAttribute,
        kAXParentAttribute,
        kAXPlaceholderValueAttribute,
        kAXRoleDescriptionAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )

    attributes = (
        kAXPlaceholderValueAttribute,
        kAXTitleAttribute,
        kAXDescriptionAttribute,
        kAXHelpAttribute,
        kAXRoleDescriptionAttribute,
        kAXValueAttribute,
    )
    texts: list[str] = []
    current = element
    for _ in range(max(1, ancestor_depth + 1)):
        if current is None:
            break
        for attribute in attributes:
            value = _copy_attribute(current, attribute)
            if isinstance(value, str) and 0 < len(value.strip()) <= 500:
                texts.append(value.strip())
        current = _copy_attribute(current, kAXParentAttribute)
    return texts


def _rect_tuple(rect) -> tuple[float, float, float, float] | None:
    if rect is None:
        return None
    try:
        return (
            float(rect.origin.x),
            float(rect.origin.y),
            float(rect.size.width),
            float(rect.size.height),
        )
    except AttributeError:
        pass
    try:
        origin, size = rect
        return (
            float(origin[0]),
            float(origin[1]),
            float(size[0]),
            float(size[1]),
        )
    except (TypeError, ValueError, IndexError):
        return None


def get_caret_frame(element) -> tuple[float, float, float, float] | None:
    """Return an AX frame using global top-left-origin coordinates."""
    if element is None:
        return None
    import ApplicationServices as ax

    # Some py2app builds expose the AX functions but omit this constant from
    # ApplicationServices.__init__. The underlying Accessibility API accepts
    # the canonical CFString attribute name, so do not make caret placement a
    # hard dependency on that Python export.
    frame_attribute = getattr(ax, "kAXFrameAttribute", "AXFrame")

    selected_range = _copy_attribute(element, ax.kAXSelectedTextRangeAttribute)
    if selected_range is not None:
        error, bounds = ax.AXUIElementCopyParameterizedAttributeValue(
            element,
            ax.kAXBoundsForRangeParameterizedAttribute,
            selected_range,
            None,
        )
        if not error and bounds is not None:
            rect = _ax_value(bounds, ax.kAXValueCGRectType)
            result = _rect_tuple(rect)
            if result is not None:
                return result

    frame_value = _copy_attribute(element, frame_attribute)
    if frame_value is None:
        return None
    return _rect_tuple(_ax_value(frame_value, ax.kAXValueCGRectType))


def capture_input_context() -> InputContext:
    try:
        element = focused_element()
    except Exception:
        return InputContext()
    if element is None:
        return InputContext()
    try:
        texts = collect_context_texts(element)
    except Exception:
        texts = []
    try:
        caret_frame = get_caret_frame(element)
    except Exception:
        caret_frame = None
    return InputContext(
        element=element,
        pid=_element_pid(element),
        detected_language=detect_language_from_texts(texts),
        caret_frame=caret_frame,
    )


def focus_matches(context: InputContext | None) -> bool:
    if context is None or context.element is None:
        return False
    current = focused_element()
    if current is None:
        return False
    try:
        return current == context.element
    except Exception:
        return _element_pid(current) == context.pid


def restore_input_focus(context: InputContext | None) -> bool:
    """Activate the original app and focus the captured input element."""
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
        activated = application.activateWithOptions_(
            NSApplicationActivateAllWindows
            | NSApplicationActivateIgnoringOtherApps
        )
        if context.element is not None:
            from ApplicationServices import (
                AXUIElementSetAttributeValue,
                kAXFocusedAttribute,
            )

            AXUIElementSetAttributeValue(
                context.element, kAXFocusedAttribute, True
            )
        return bool(activated)
    except Exception:
        return False


def insert_text_at_context(
    context: InputContext | None, text: str
) -> str | None:
    """Attempt direct AX insertion, otherwise let the caller simulate paste."""
    if context is None or context.element is None or not text:
        return None
    try:
        from ApplicationServices import (
            AXUIElementSetAttributeValue,
            kAXSelectedTextAttribute,
        )

        error = AXUIElementSetAttributeValue(
            context.element, kAXSelectedTextAttribute, text
        )
        return "accessibility" if not error else None
    except Exception:
        return None


def accessibility_is_trusted(prompt: bool = False) -> bool:
    try:
        if prompt:
            from ApplicationServices import (
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            return bool(
                AXIsProcessTrustedWithOptions(
                    {kAXTrustedCheckOptionPrompt: True}
                )
            )
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return False
