"""Windows Explorer file drops onto a Tk window, with ctypes only (no tkinterdnd2).

    enable_drops(root, on_files)     # on_files(list_of_paths) runs in Tk's thread

It sets WS_EX_ACCEPTFILES on the toplevel's frame window and subclasses its window procedure to catch WM_DROPFILES.
The shell delivers a drop to the nearest window up the parent chain that accepts files, so drops anywhere on the
window arrive here.
"""
import ctypes
import ctypes.wintypes as W
import sys

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

if sys.platform == 'win32':
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32')
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _set = getattr(user32, 'SetWindowLongPtrW', None) or user32.SetWindowLongW
    _set.restype = ctypes.c_void_p
    _set.argtypes = [W.HWND, ctypes.c_int, ctypes.c_void_p]
    shell32.DragQueryFileW.restype = W.UINT
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, W.UINT, ctypes.c_wchar_p, W.UINT]
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    shell32.DragAcceptFiles.argtypes = [W.HWND, W.BOOL]
    # let drops from a non-elevated Explorer reach us even if this process runs elevated
    user32.ChangeWindowMessageFilterEx.argtypes = [W.HWND, W.UINT, W.DWORD, ctypes.c_void_p]

_keep = []      # the callbacks must outlive the window


def _files(hdrop):
    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out = []
    for i in range(n):
        size = shell32.DragQueryFileW(hdrop, i, None, 0) + 1
        buf = ctypes.create_unicode_buffer(size)
        shell32.DragQueryFileW(hdrop, i, buf, size)
        out.append(buf.value)
    return out


def enable_drops(root, on_files, poll_ms=100):
    """Returns True when drops were enabled (Windows only).

    The window procedure runs inside Tk's own event processing, where calling any Tk function (even root.after)
    corrupts tkinter's thread state (Python 3.14: "PyEval_RestoreThread ... GIL is released", a fatal error). So it
    only queues the paths, and a Tk timer hands them to on_files. CallWindowProcW goes through ctypes, which releases
    the GIL, so Tk's own handlers can still call into Python."""
    if sys.platform != 'win32':
        return False
    import queue
    dropped = queue.SimpleQueue()
    root.update_idletasks()
    hwnd = int(root.wm_frame(), 16)
    for msg in (WM_DROPFILES, 0x0049, 0x004A):          # WM_DROPFILES, WM_COPYGLOBALDATA, WM_COPYDATA
        user32.ChangeWindowMessageFilterEx(hwnd, msg, 1, None)
    old = None

    def proc(h, msg, wp, lp):
        if msg == WM_DROPFILES:
            try:
                dropped.put(_files(wp))
            finally:
                shell32.DragFinish(wp)
            return 0
        return user32.CallWindowProcW(old, h, msg, wp, lp)

    def poll():
        root._drop_timer = root.after(poll_ms, poll)     # first, so an error below can't stop later drops
        paths = []
        while not dropped.empty():
            paths += dropped.get()
        if paths:
            on_files(paths)

    cb = WNDPROC(proc)
    _keep.append(cb)
    old = _set(hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
    shell32.DragAcceptFiles(hwnd, True)
    root._drop_timer = root.after(poll_ms, poll)
    return True
