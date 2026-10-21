"""Native macOS chrome for the pywebview window: a transparent title bar whose traffic lights sit in the app's
own header, the system's translucent window material behind the page, and a Chinese menu bar with shortcuts.

The page reports which part of its header is free to drag the window by (see ui/src/native.ts); clicks there
move the window natively instead of reaching the page.
"""

import json

import AppKit
import objc
from PyObjCTools import AppHelper
from webview.platforms.cocoa import BrowserView

APPEARANCES = {"light": AppKit.NSAppearanceNameAqua, "dark": AppKit.NSAppearanceNameDarkAqua}

# The header strip the window can be dragged by, minus the page's controls in it, in page points.
_drag = {"height": 0.0, "controls": []}
_original_mouse_down = BrowserView.WebKitHost.__dict__["mouseDown_"].callable


def _drag_point(view, event) -> bool:
    point = view.convertPoint_fromView_(event.locationInWindow(), None)
    x, y = point.x, point.y if view.isFlipped() else view.bounds().size.height - point.y
    if y >= _drag["height"]:
        return False
    return not any(left <= x <= left + width and top <= y <= top + height for left, top, width, height in _drag["controls"])


def _mouse_down(self, event):
    window = self.window()
    if window is None or not _drag_point(self, event):
        return _original_mouse_down(self, event)
    if event.clickCount() == 2:  # double-clicking a title bar does what System Settings says
        action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
        if action == "Minimize":
            window.performMiniaturize_(None)
        elif action != "None":
            window.performZoom_(None)
        return
    window.performWindowDragWithEvent_(event)


objc.classAddMethods(BrowserView.WebKitHost, [objc.selector(_mouse_down, selector=b"mouseDown:", signature=b"v@:@")])


class Api:
    """Called from the page (window.pywebview.api) on a pywebview worker thread."""

    def __init__(self, shell: "NativeShell") -> None:
        self._shell = shell

    def set_drag_regions(self, height, controls):
        _drag["height"], _drag["controls"] = float(height), [tuple(map(float, c)) for c in controls]

    def ui_state(self, state):
        AppHelper.callAfter(self._shell.show_state, dict(state))


class MenuTarget(AppKit.NSObject):
    def initWithHandler_(self, handler):
        self = objc.super(MenuTarget, self).init()
        self.handler = handler
        return self

    def perform_(self, sender):
        self.handler(sender.representedObject())


class NativeShell:
    """Applies the chrome once the page is in the window and relays menu commands to it."""

    def __init__(self, window) -> None:
        self.window = window
        self.applied = False
        self.target = MenuTarget.alloc().initWithHandler_(self.send)
        self.checks: dict[str, list] = {}  # state key -> [(value, menu item)], for check marks

    # ----- window -----

    def on_loaded(self) -> None:
        AppHelper.callAfter(self._apply)

    def _apply(self) -> None:
        ns = self.window.native
        webview = ns.contentView()
        if not self.applied:
            self.applied = True
            # pywebview paints the title bar opaque for ordinary windows; let the page show through it.
            titlebar = ns.contentView().superview().subviews().lastObject()
            if titlebar is not webview and titlebar.respondsToSelector_("setBackgroundColor:"):
                titlebar.setBackgroundColor_(AppKit.NSColor.clearColor())
            ns.setStyleMask_(ns.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
            ns.setTitlebarAppearsTransparent_(True)
            ns.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("main")
            toolbar.setShowsBaselineSeparator_(False)
            ns.setToolbar_(toolbar)
            ns.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
            ns.setOpaque_(False)
            ns.setBackgroundColor_(AppKit.NSColor.clearColor())
            material = AppKit.NSVisualEffectView.alloc().initWithFrame_(webview.bounds())
            material.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            material.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
            material.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            material.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
            webview.addSubview_positioned_relativeTo_(material, AppKit.NSWindowBelow, None)
            webview.setValue_forKey_(True, "drawsTransparentBackground")
            self._install_menu()
        self.js("document.documentElement.dataset.shell = 'native'")

    def js(self, script: str) -> None:
        """Run script in the page; called on the main thread, so it does not wait for a result."""
        self.window.native.contentView().evaluateJavaScript_completionHandler_(script, None)

    def send(self, command: str) -> None:
        self.js(f"window.dispatchEvent(new CustomEvent('app-command', {{ detail: {json.dumps(command)} }}))")

    def show_state(self, state: dict) -> None:
        """The page's theme and switches: window appearance (which tints the material) and menu check marks."""
        theme = state.get("theme")
        if theme:
            name = APPEARANCES.get(theme)
            self.window.native.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name) if name else None)
        for key, items in self.checks.items():
            if key in state:
                for value, item in items:
                    item.setState_(AppKit.NSControlStateValueOn if state[key] == value else AppKit.NSControlStateValueOff)

    # ----- menu bar -----

    def _install_menu(self) -> None:
        app = AppKit.NSApplication.sharedApplication()
        bar = AppKit.NSMenu.alloc().init()
        name = "MLX Agents"
        self._menu(bar, name, [
            (f"关于 {name}", "orderFrontStandardAboutPanel:", ""),
            None,
            ("设置…", "settings", ","),
            None,
            ("服务", "services", ""),
            None,
            (f"隐藏 {name}", "hide:", "h"),
            ("隐藏其他", "hideOtherApplications:", "⌥h"),
            ("全部显示", "unhideAllApplications:", ""),
            None,
            (f"退出 {name}", "terminate:", "q"),
        ], app)
        self._menu(bar, "文件", [("新对话", "new-chat", "n"), None, ("关闭窗口", "performClose:", "w")], app)
        self._menu(bar, "编辑", [
            ("撤销", "undo:", "z"), ("重做", "redo:", "⇧z"), None,
            ("剪切", "cut:", "x"), ("拷贝", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a"),
        ], app)
        self._menu(bar, "显示", [
            ("对话", "tab-chat", "1"), ("作品库", "tab-gallery", "2"), None,
            ("外观", [("跟随系统", "theme:system", ""), ("浅色", "theme:light", ""), ("深色", "theme:dark", "")]),
            ("减少动效", "calm:toggle", ""), None,
            ("进入全屏幕", "toggleFullScreen:", "⌃f"),
        ], app)
        self._menu(bar, "对话", [("停止任务", "stop", "."), None, ("自动批准", "auto:toggle", "")], app)
        windows = self._menu(bar, "窗口", [
            ("最小化", "performMiniaturize:", "m"), ("缩放", "performZoom:", ""), None, ("前置全部窗口", "arrangeInFront:", ""),
        ], app)
        app.setMainMenu_(bar)
        app.setWindowsMenu_(windows)

    def _menu(self, bar, title: str, entries: list, app):
        menu = AppKit.NSMenu.alloc().initWithTitle_(title)
        holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        holder.setSubmenu_(menu)
        bar.addItem_(holder)
        self._fill(menu, entries, app)
        return menu

    def _fill(self, menu, entries: list, app) -> None:
        for entry in entries:
            if entry is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            title, action = entry[0], entry[1]
            if isinstance(action, list):  # a submenu
                sub = AppKit.NSMenu.alloc().initWithTitle_(title)
                item = menu.addItemWithTitle_action_keyEquivalent_(title, None, "")
                item.setSubmenu_(sub)
                self._fill(sub, action, app)
                continue
            key = entry[2]
            mask = AppKit.NSEventModifierFlagCommand
            for symbol, flag in (("⌥", AppKit.NSEventModifierFlagOption), ("⇧", AppKit.NSEventModifierFlagShift), ("⌃", AppKit.NSEventModifierFlagControl)):
                if symbol in key:
                    key, mask = key.replace(symbol, ""), mask | flag
            if action == "services":
                item = menu.addItemWithTitle_action_keyEquivalent_(title, None, "")
                services = AppKit.NSMenu.alloc().initWithTitle_(title)
                item.setSubmenu_(services)
                app.setServicesMenu_(services)
                continue
            if action.endswith(":"):  # a standard action, sent along the responder chain
                item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            else:
                item = menu.addItemWithTitle_action_keyEquivalent_(title, "perform:", key)
                item.setTarget_(self.target)
                item.setRepresentedObject_(action)
                state, _, value = action.partition(":")
                if value and value != "toggle":
                    self.checks.setdefault(state, []).append((value, item))
                elif value == "toggle":
                    self.checks.setdefault(state, []).append((True, item))
            item.setKeyEquivalentModifierMask_(mask)


def install(window) -> NativeShell:
    shell = NativeShell(window)
    api = Api(shell)
    window.expose(api.set_drag_regions, api.ui_state)
    window.events.loaded += shell.on_loaded
    return shell
