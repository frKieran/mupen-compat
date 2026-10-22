"""Build dist/MupenCompat.exe: one file, no Python needed to run it.

    pip install pyinstaller==6.22.3 pillow==12.3.0   (the versions the release was built with)
    python build.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'build')


def make_icon(path):
    """The icon: a film-strip square with 'M64' and a back arrow."""
    from PIL import Image, ImageDraw, ImageFont
    s = 256
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8, 8, s - 8, s - 8), radius=44, fill=(30, 90, 170, 255))
    for x in range(36, s - 30, 40):
        d.rounded_rectangle((x, 22, x + 20, 40), radius=4, fill=(255, 255, 255, 200))
        d.rounded_rectangle((x, s - 40, x + 20, s - 22), radius=4, fill=(255, 255, 255, 200))
    try:
        font = ImageFont.truetype(os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'segoeuib.ttf'), 92)
    except OSError:
        font = ImageFont.load_default()
    d.text((s // 2, s // 2 - 12), 'M64', font=font, fill='white', anchor='mm')
    d.polygon([(62, 196), (100, 172), (100, 220)], fill=(255, 210, 60, 255))
    d.rectangle((96, 189, 196, 203), fill=(255, 210, 60, 255))
    im.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def main():
    os.makedirs(BUILD, exist_ok=True)
    icon = make_icon(os.path.join(BUILD, 'MupenCompat.ico'))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
                    '--name', 'MupenCompat', '--icon', icon, '--paths', HERE,
                    '--workpath', BUILD, '--specpath', BUILD, '--distpath', os.path.join(HERE, 'dist'),
                    '--exclude-module', 'numpy', '--exclude-module', 'PIL',
                    os.path.join(HERE, 'MupenCompat.pyw')], check=True)
    print('built', os.path.join(HERE, 'dist', 'MupenCompat.exe'))


if __name__ == '__main__':
    main()
