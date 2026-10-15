"""python -m mupencompat [FILE...]          the window (files dropped on the exe arrive as arguments)
python -m mupencompat --report FILE...   command line (see cli.py)"""
import sys


def run():
    args = sys.argv[1:]
    if any(a.startswith('-') for a in args):
        from .cli import main
        sys.exit(main([a for a in args if a != '--report']))
    from .gui import main
    main(args)


if __name__ == '__main__':
    run()
