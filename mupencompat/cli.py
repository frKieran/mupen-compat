"""Command line: python -m mupencompat [--target VERSION] [--convert] [--out DIR | --replace] FILE..."""
import argparse
import sys

from . import formats as F
from . import report
from .convert import Job, run_jobs


def _attach_console():
    """The exe is a window app; when run from a terminal with options, print into that terminal."""
    import ctypes
    if ctypes.windll.kernel32.AttachConsole(-1):              # ATTACH_PARENT_PROCESS
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace')
        sys.stderr = sys.stdout
        print()


def main(argv=None):
    ap = argparse.ArgumentParser(prog='mupencompat', description=__doc__)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--target', default=F.ANY, help=f'a Mupen version ({F.OLDEST} .. {F.NEWEST}) or "any"')
    ap.add_argument('--report', action='store_true', help='only report (the default without --convert)')
    ap.add_argument('--convert', action='store_true', help='write converted files (default: only report)')
    ap.add_argument('--out', help='folder for converted copies (default: a "for Mupen X" folder beside each file)')
    ap.add_argument('--replace', action='store_true', help='overwrite the originals (a .bak copy is kept)')
    ap.add_argument('--no-backup', action='store_true', help='with --replace: keep no .bak copy')
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        _attach_console()
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    if a.target != F.ANY and a.target not in F.VERSIONS:
        ap.error(f'unknown version {a.target}; known: {", ".join(F.VERSIONS)}')
    if not a.convert:
        for p in a.files:
            try:
                print(report.describe(F.analyze(p), a.target))
            except (OSError, F.FormatError) as ex:
                print(f'{p}: {ex}')
            print()
        return 0
    jobs = [Job(p) for p in a.files]
    results = run_jobs(jobs, a.target, mode='replace' if a.replace else 'copy', out_dir=a.out,
                       backup=not a.no_backup)
    bad = 0
    for r in results:
        print(r.text())
        bad += r.status == 'error'
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
