"""Mupen Compat window: drop movies and savestates, pick the Mupen version they must work in, convert."""
import json
import re
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from . import formats as F
from . import report
from .convert import Job, add_pairs, pair_in_batch, run_jobs
from .dnd import enable_drops

APP = 'Mupen Compat'
EXTS = ('.m64', '.st', '.savestate')
ANY_LABEL = 'Every version (most compatible)'
SETTINGS = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MupenCompat', 'settings.json')
FILETYPES = [('Movies and savestates', '*.m64 *.st *.savestate'), ('Movies', '*.m64'),
             ('Savestates', '*.st *.savestate'), ('All files', '*.*')]
COLORS = {'ok': '#1a7f37', 'convert': '#0b5cad', 'warn': '#9a6700', 'error': '#c62828'}
STATUS_WORD = {'ok': 'Works already', 'convert': 'Will convert', 'warn': 'Works (see note)', 'error': 'Problem'}


DEFAULTS = {'target': ANY_LABEL, 'include_pairs': True, 'mode': 'copy', 'out_choice': 'beside', 'out_dir': '',
            'backup': True, 'geometry': '1040x700'}
CHOICES = {'mode': ('copy', 'replace'), 'out_choice': ('beside', 'folder', 'tagged'),
           'target': [ANY_LABEL] + [f'Mupen {v}' for v in F.VERSIONS]}


def load_settings():
    """The saved settings, each value checked: anything damaged, of the wrong type or unknown falls back to its
    default (a hand-edited or old file must never stop the window from opening)."""
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    out = {}
    for k, default in DEFAULTS.items():
        v = raw.get(k, default)
        if type(v) is not type(default) or (k in CHOICES and v not in CHOICES[k]):
            v = default
        out[k] = v
    return out


def safe_geometry(root, g):
    """The saved size and position, kept on a visible screen (a monitor may have been unplugged since)."""
    m = re.fullmatch(r'(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?', g or '')    # Tk writes +-N for a negative x / y
    if not m:
        return DEFAULTS['geometry']
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = max(400, min(int(m.group(1)), sw)), max(300, min(int(m.group(2)), sh))
    if m.group(3) is None:
        return f'{w}x{h}'
    x, y = int(m.group(3)), int(m.group(4))
    if not _on_a_monitor(x + 60, y + 12, sw, sh):           # the title bar must be on a connected screen
        return f'{w}x{h}'                                   # let Windows place it
    return f'{w}x{h}+{x}+{y}'


def _on_a_monitor(x, y, sw, sh):
    if sys.platform == 'win32':
        import ctypes
        import ctypes.wintypes as W
        f = ctypes.windll.user32.MonitorFromPoint
        f.argtypes, f.restype = [W.POINT, W.DWORD], ctypes.c_void_p
        return bool(f(W.POINT(x, y), 0))                    # MONITOR_DEFAULTTONULL
    return 0 <= x < sw and 0 <= y < sh


def save_settings(s):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


class App:
    def __init__(self, root, files=()):
        self.root = root
        self.jobs = []
        self.busy = False
        self.results = queue.Queue()
        s = load_settings()
        root.title(APP)
        if getattr(sys, 'frozen', False):
            try:
                root.iconbitmap(default=sys.executable)     # the exe's own icon instead of Tk's feather
            except tk.TclError:
                pass
        root.minsize(860, 600)
        root.geometry(safe_geometry(root, s['geometry']))
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        self.target = tk.StringVar(value=s.get('target', ANY_LABEL))
        self.include_pairs = tk.BooleanVar(value=s.get('include_pairs', True))
        self.mode = tk.StringVar(value=s.get('mode', 'copy'))
        self.out_choice = tk.StringVar(value=s.get('out_choice', 'beside'))
        self.out_dir = tk.StringVar(value=s.get('out_dir', ''))
        self.backup = tk.BooleanVar(value=s.get('backup', True))
        self._build()
        self.drops = enable_drops(root, self.add_paths)
        if not self.drops:
            self.drop.config(text='Click here to choose .m64 movies and .st savestates')
        root.protocol('WM_DELETE_WINDOW', self.close)
        if files:
            root.after(100, self.add_paths, list(files))

    # ---------------------------------------------------------------- layout
    def _build(self):
        r = self.root
        top = ttk.Frame(r, padding=(12, 10, 12, 0))
        top.pack(fill='x')
        self.drop = tk.Label(top, text='Drop .m64 movies and .st savestates here\nor click to choose files',
                             font=('Segoe UI', 13), fg='#444', bg='#eef3fa', relief='groove', bd=2, height=3,
                             cursor='hand2')
        self.drop.pack(fill='x')
        self.drop.bind('<Button-1>', lambda e: self.pick_files())
        self.drop.bind('<Enter>', lambda e: self.drop.config(bg='#e1ebf7'))
        self.drop.bind('<Leave>', lambda e: self.drop.config(bg='#eef3fa'))

        bar = ttk.Frame(r, padding=(12, 6, 12, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text='Remove selected', command=self.remove_selected).pack(side='left')
        ttk.Button(bar, text='Clear', command=self.clear).pack(side='left', padx=(6, 0))
        ttk.Checkbutton(bar, text="Include each movie's savestate", variable=self.include_pairs,
                        command=self.refresh_pairs).pack(side='right')

        mid = ttk.PanedWindow(r, orient='vertical')
        mid.pack(fill='both', expand=True, padx=12, pady=(6, 0))
        tf = ttk.Frame(mid)
        cols = ('type', 'made', 'opens', 'status')
        self.tree = ttk.Treeview(tf, columns=cols, selectmode='extended')
        for c, text, w in (('#0', 'File', 230), ('type', 'Type', 200), ('made', 'Made by', 220),
                           ('opens', 'Opens in', 200), ('status', 'For the chosen version', 150)):
            self.tree.heading(c, text=text, anchor='w')
            self.tree.column(c, width=w, minwidth=60, stretch=True, anchor='w')
        for tag, col in COLORS.items():
            self.tree.tag_configure(tag, foreground=col)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_details())
        self.tree.bind('<Delete>', lambda e: self.remove_selected())
        mid.add(tf, weight=3)
        df = ttk.LabelFrame(mid, text='Details', padding=4)
        self.details = ScrolledText(df, height=9, wrap='word', font=('Consolas', 9), relief='flat',
                                    state='disabled', bg='#fbfbfb')
        self.details.pack(fill='both', expand=True)
        mid.add(df, weight=2)

        opt = ttk.Frame(r, padding=(12, 8, 12, 0))
        opt.pack(fill='x')
        ttk.Label(opt, text='Make them work in:').grid(row=0, column=0, sticky='w')
        self.combo = ttk.Combobox(opt, textvariable=self.target, state='readonly', width=34,
                                  values=[ANY_LABEL] + [f'Mupen {v}' for v in reversed(F.VERSIONS)])
        if self.target.get() not in self.combo['values']:
            self.target.set(ANY_LABEL)
        self.combo.grid(row=0, column=1, sticky='w', padx=(6, 0))
        self.combo.bind('<<ComboboxSelected>>', lambda e: self._mode_changed())
        ttk.Label(opt, text='(Mupen shows its version in Help > About)', foreground='#666').grid(
            row=0, column=2, sticky='w', padx=(8, 0))

        out = ttk.LabelFrame(r, text='Output', padding=(8, 4))
        out.pack(fill='x', padx=12, pady=(8, 0))
        ttk.Radiobutton(out, text='Make new copies', value='copy', variable=self.mode,
                        command=self._mode_changed).grid(row=0, column=0, sticky='w')
        self.r_beside = ttk.Radiobutton(out, text='in a "for Mupen …" folder next to each file', value='beside',
                                        variable=self.out_choice, command=self._mode_changed)
        self.r_beside.grid(row=0, column=1, sticky='w', padx=(16, 0))
        self.r_folder = ttk.Radiobutton(out, text='in this folder:', value='folder', variable=self.out_choice,
                                        command=self._mode_changed)
        self.r_folder.grid(row=1, column=1, sticky='w', padx=(16, 0))
        self.e_folder = ttk.Entry(out, textvariable=self.out_dir, width=50)
        self.e_folder.grid(row=1, column=2, sticky='we', padx=(4, 0))
        self.b_folder = ttk.Button(out, text='Browse…', command=self.pick_out_dir)
        self.b_folder.grid(row=1, column=3, padx=(4, 0))
        self.r_tagged = ttk.Radiobutton(out, text='next to each original, with the version in the name',
                                        value='tagged', variable=self.out_choice, command=self._mode_changed)
        self.r_tagged.grid(row=2, column=1, sticky='w', padx=(16, 0))
        self.l_tagged = ttk.Label(out, foreground='#666')
        self.l_tagged.grid(row=2, column=2, columnspan=2, sticky='w', padx=(4, 0))
        ttk.Radiobutton(out, text='Replace the original files', value='replace', variable=self.mode,
                        command=self._mode_changed).grid(row=3, column=0, sticky='w', pady=(4, 0))
        self.c_backup = ttk.Checkbutton(out, text='keep a backup of each original (NAME.bak)', variable=self.backup)
        self.c_backup.grid(row=3, column=1, columnspan=2, sticky='w', padx=(16, 0), pady=(4, 0))
        out.columnconfigure(2, weight=1)

        bottom = ttk.Frame(r, padding=(12, 8, 12, 10))
        bottom.pack(fill='x')
        self.status = ttk.Label(bottom, text='Drop files on the window to begin.', foreground='#444')
        self.status.pack(side='left')
        self.b_convert = ttk.Button(bottom, text='Convert', command=self.convert)
        self.b_convert.pack(side='right')
        ttk.Button(bottom, text='About', command=self.about).pack(side='right', padx=(0, 6))
        # the options and buttons claim their space first, so a small window shrinks the list, never the buttons
        for w in (bottom, out, opt):
            w.pack_configure(side='bottom', before=mid)
        self._mode_changed()

    def _mode_changed(self):
        copy = self.mode.get() == 'copy'
        folder = copy and self.out_choice.get() == 'folder'
        for w in (self.r_beside, self.r_folder, self.r_tagged):
            w.state(['!disabled'] if copy else ['disabled'])
        from .convert import appended, prefixed
        t = self.target_value()
        self.l_tagged.config(text=f'e.g. {appended("run.m64", t)}, or {prefixed("run.m64", t)} + '
                                  f'{prefixed("run.st", t)} (savestate)')
        for w in (self.e_folder, self.b_folder):
            w.state(['!disabled'] if folder else ['disabled'])
        self.c_backup.state(['disabled'] if copy else ['!disabled'])
        self.refresh_status()

    # ---------------------------------------------------------------- helpers
    def target_value(self):
        t = self.target.get()
        return F.ANY if t == ANY_LABEL else t.replace('Mupen ', '')

    def say(self, text):
        self.status.config(text=text)

    def job_for(self, iid):
        return next((j for j in self.jobs if j.path == iid), None)

    # ---------------------------------------------------------------- adding files
    def pick_files(self):
        paths = filedialog.askopenfilenames(parent=self.root, title='Choose movies and savestates',
                                            filetypes=FILETYPES)
        if paths:
            self.add_paths(list(paths))

    def pick_out_dir(self):
        d = filedialog.askdirectory(parent=self.root, title='Folder for the converted copies',
                                    initialdir=self.out_dir.get() or None)
        if d:
            self.out_dir.set(d)
            self.out_choice.set('folder')
            self._mode_changed()

    def add_paths(self, paths):
        files = []
        for p in paths:
            if os.path.isdir(p):
                for root, _, names in os.walk(p):
                    files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(EXTS)]
            elif os.path.isfile(p):
                files.append(p)
        have = {os.path.normcase(j.path) for j in self.jobs if not j.auto}
        new = []
        for f in files:
            f = os.path.normpath(os.path.abspath(f))
            if os.path.normcase(f) not in have:
                have.add(os.path.normcase(f))
                new.append(f)
        if not new:
            self.say('Those files are already in the list.' if files else 'No files found there.')
            return
        self.say(f'Reading {len(new)} file(s)…')
        self.root.config(cursor='watch')
        self.root.update_idletasks()
        for i, f in enumerate(new):
            # an explicit add replaces an automatic pair entry for the same file
            self.jobs = [j for j in self.jobs if not (j.auto and os.path.normcase(j.path) == os.path.normcase(f))]
            self.jobs.append(Job(f))
            if i % 20 == 19:
                self.say(f'Reading… {i + 1}/{len(new)}')
                self.root.update_idletasks()
        self.root.config(cursor='')
        self.refresh_pairs()
        n_bad = sum(1 for j in self.jobs if j.error)
        self.say(f'{len(self.jobs)} file(s) in the list' + (f', {n_bad} unreadable' if n_bad else '') + '.')

    def refresh_pairs(self):
        self.jobs = [j for j in self.jobs if not j.auto]
        if self.include_pairs.get():
            add_pairs(self.jobs)
        self.refresh_tree()

    def remove_selected(self):
        sel = set(self.tree.selection())
        self.jobs = [j for j in self.jobs if j.path not in sel]
        self.refresh_pairs()

    def clear(self):
        self.jobs = []
        self.refresh_tree()
        self.say('Drop files on the window to begin.')

    # ---------------------------------------------------------------- showing
    def type_text(self, j):
        if j.error:
            return '?'
        if j.kind == 'movie':
            i = j.info
            start = 'savestate' if i.start_flags & F.START_SNAPSHOT else \
                'EEPROM' if i.start_flags & F.START_EEPROM else 'power-on'
            return f'movie (starts from {start})'
        movie = next((m for m in self.jobs if m.kind == 'movie' and m.info and pair_in_batch(m, self.jobs) == j.path),
                     None)
        if movie:
            return f'savestate of {os.path.basename(movie.path)}'
        return 'savestate'

    def refresh_tree(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        target = self.target_value()
        for j in self.jobs:
            name = os.path.basename(j.path) + ('  (paired)' if j.auto else '')
            if j.error:
                vals, tag = ('?', '—', '—', 'Unreadable'), 'error'
            else:
                st, short = report.status_for(j.info, target)
                vals = (self.type_text(j), j.info.made_by(), F.describe_ranges(j.info.opens_in()),
                        STATUS_WORD[st])
                tag = st
            self.tree.insert('', 'end', iid=j.path, text=name, values=vals, tags=(tag,))
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        elif self.jobs:
            self.tree.selection_set(self.jobs[-1].path)
        self.show_details()
        n_conv = sum(1 for j in self.jobs if not j.error and report.status_for(j.info, target)[0] == 'convert')
        self.b_convert.config(text=f'Convert {n_conv} file(s)' if n_conv else 'Convert')

    def refresh_status(self):
        if self.jobs:
            self.refresh_tree()

    def show_details(self):
        sel = self.tree.selection()
        text = ''
        if sel:
            j = self.job_for(sel[0])
            if j and j.error:
                text = f'{j.path}\n\nCan\'t read this file: {j.error}'
            elif j:
                text = report.describe(j.info, self.target_value())
                if j.kind == 'movie' and j.info.start_flags & F.START_SNAPSHOT:
                    st = pair_in_batch(j, self.jobs) or F.find_state(j.path)
                    text += '\n\nSavestate: ' + (st if st else 'none found next to the movie (Mupen looks for ' +
                                                 ', '.join(os.path.basename(c) for c in F.state_candidates(j.path)
                                                           [:4]) + ' …)')
        elif not self.jobs:
            text = ('Drop Mupen64-rr-lua movies (.m64) and savestates (.st) on this window. Mupen Compat shows which '
                    'Mupen versions can open each one, and converts copies for an older version:\n'
                    '  • savestates from 1.5.0 and newer are LZ4-compressed, which older versions read as "from '
                    'another ROM" (converted to gzip);\n'
                    '  • movies from 1.5.0 and newer have extended header version 4, which 1.1.9 – 1.4.0-8 reject '
                    'as an invalid extended version (lowered to 1);\n'
                    '  • screenshots inside savestates changed format in 1.4.0 (removed for older versions).\n'
                    'A movie keeps its inputs exactly; whether it syncs in the older version also depends on the '
                    'emulator settings, which the details list when the movie records them.')
        self.details.config(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.config(state='disabled')

    # ---------------------------------------------------------------- converting
    def convert(self):
        if self.busy:
            return
        jobs = [j for j in self.jobs]
        if not jobs:
            self.say('Drop some files first.')
            return
        target = self.target_value()
        mode = self.mode.get()
        if mode == 'copy' and self.out_choice.get() == 'tagged':
            mode = 'tagged'
        out_dir = None
        if mode == 'copy' and self.out_choice.get() == 'folder':
            out_dir = self.out_dir.get().strip()
            if not out_dir:
                messagebox.showwarning(APP, 'Choose a folder for the copies, or pick "next to each file".',
                                       parent=self.root)
                return
        todo = [j for j in jobs if not j.error and report.status_for(j.info, target)[0] == 'convert']
        if not todo:
            messagebox.showinfo(APP, f'Nothing to convert: every readable file already works in '
                                     f'{F.target_label(target)}.', parent=self.root)
            return
        if mode == 'replace':
            msg = f'Overwrite {len(todo)} original file(s) with versions for {F.target_label(target)}?'
            msg += '\nA .bak copy of each will be kept.' if self.backup.get() else '\nNo backups will be kept.'
            if not messagebox.askyesno(APP, msg, parent=self.root):
                return
        self.busy = True
        self.b_convert.state(['disabled'])
        self.say('Converting…')
        backup = self.backup.get()                         # Tk variables are for the main thread only

        def work():
            try:
                res = run_jobs(jobs, target, mode=mode, out_dir=out_dir, backup=backup)
                self.results.put(('ok', res))
            except Exception as ex:                        # noqa: BLE001 - reported in a message box
                self.results.put(('fail', ex))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self._poll, target, mode, out_dir)

    def _poll(self, target, mode, out_dir):
        try:
            kind, res = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll, target, mode, out_dir)
            return
        self.busy = False
        self.b_convert.state(['!disabled'])
        if kind == 'fail':
            self.say('Conversion failed.')
            messagebox.showerror(APP, f'Conversion failed:\n{res}', parent=self.root)
            return
        n_conv = sum(r.status in ('converted', 'renamed') for r in res)
        n_err = sum(r.status == 'error' for r in res)
        n_skip = sum(r.status == 'unchanged' for r in res)
        self.say(f'Converted {n_conv} file(s)' + (f', {n_err} failed' if n_err else '') +
                 (f', {n_skip} already worked (skipped, no copy)' if n_skip else '') + '.')
        folders = sorted({os.path.dirname(r.out) for r in res if r.out and r.status in ('converted', 'renamed')})
        self.show_results(res, target, folders)
        if mode == 'replace':          # the list now shows the new files
            paths = [j.path for j in self.jobs if not j.auto]
            self.jobs = []
            self.add_paths(paths)

    def show_results(self, res, target, folders):
        w = tk.Toplevel(self.root)
        w.title(f'{APP}: results')
        self.root.update_idletasks()             # open over the main window, not wherever Windows chooses
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 760) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - 420) // 3)
        w.geometry(f'760x420+{x}+{y}')
        w.transient(self.root)
        t = ScrolledText(w, wrap='word', font=('Consolas', 9))
        t.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        t.insert('1.0', f'For {F.target_label(target)}:\n\n' + '\n\n'.join(r.text() for r in res))
        t.config(state='disabled')
        b = ttk.Frame(w, padding=8)
        b.pack(fill='x')
        ttk.Button(b, text='Close', command=w.destroy).pack(side='right')
        for f in folders[:3]:
            ttk.Button(b, text=f'Open {os.path.basename(f) or f}',
                       command=lambda f=f: open_folder(f)).pack(side='left', padx=(0, 6))

    def about(self):
        messagebox.showinfo(APP, f'{APP} {__version__}\n\nMakes Mupen64-rr-lua movies (.m64) and savestates (.st) '
                                 f'work in older Mupen versions ({F.OLDEST} to {F.NEWEST}).\n\nIt reads each file, '
                                 'shows which versions open it, and writes converted copies. Inputs and game state '
                                 'are never changed: only the parts of the file format an older Mupen reads '
                                 'differently.', parent=self.root)

    def close(self):
        save_settings({'target': self.target.get(), 'include_pairs': self.include_pairs.get(),
                       'mode': self.mode.get(), 'out_choice': self.out_choice.get(), 'out_dir': self.out_dir.get(),
                       'backup': self.backup.get(), 'geometry': self.root.geometry()})
        timer = getattr(self.root, '_drop_timer', None)
        if timer:
            self.root.after_cancel(timer)
        self.root.destroy()


def open_folder(path):
    if sys.platform == 'win32':
        os.startfile(path)                                   # noqa: S606 - opens Explorer on our own output
    else:
        subprocess.Popen(['xdg-open', path])


def main(argv=None):
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    App(root, files=(argv if argv is not None else sys.argv[1:]))
    root.mainloop()


if __name__ == '__main__':
    main()
