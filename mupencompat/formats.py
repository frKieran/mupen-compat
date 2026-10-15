"""Mupen64-rr-lua .m64 movies and .st savestates: which versions read them, and conversion for older versions.

Every rule here comes from the emulator's own source, release by release (mupen64/mupen64-rr-lua, tags 1.0.0 to
1.5.0-6; the analysis is in ../FORMATS.md), and the ones that matter were checked in the real emulators
(see FORMATS.md).

    info = analyze('movie.m64')             # MovieInfo / StateInfo: what the file is, who made it, who reads it
    data, notes = convert(info, '1.3.0-11') # new bytes + what changed (None when nothing needs to change)
"""
import gzip
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from .lz4frame import lz4_frame_decompress  # LZ4 frames in plain Python (no library needed)

# ---------------------------------------------------------------- versions

# Every release tag, oldest first (git tag --sort=creatordate on mupen64/mupen64-rr-lua)
VERSIONS = [
    '1.0.0', '1.0.1', '1.0.2', '1.0.3', '1.0.4', '1.0.5', '1.0.6', '1.0.7', '1.0.8', '1.0.9', '1.0.10',
    '1.1.1', '1.1.2', '1.1.3', '1.1.4', '1.1.5', '1.1.6', '1.1.7', '1.1.8', '1.1.8-2',
    '1.1.9', '1.1.9-2', '1.1.9-3', '1.1.9-4', '1.1.9-5', '1.1.9-6', '1.1.9-7', '1.1.9-8',
    '1.2.0',
    '1.3.0', '1.3.0-2', '1.3.0-3', '1.3.0-4', '1.3.0-5', '1.3.0-6', '1.3.0-7', '1.3.0-8', '1.3.0-9', '1.3.0-10',
    '1.3.0-11', '1.3.0-12', '1.3.0-13', '1.3.0-14', '1.3.0-15', '1.3.0-16', '1.3.0-17', '1.3.0-18', '1.3.0-19',
    '1.3.0-20',
    '1.4.0-beta-b4f8f172', '1.4.0-beta-90e2204a', '1.4.0-beta-3e07cf66', '1.4.0-beta-5273c500',
    '1.4.0-beta-8621a573', '1.4.0-beta-93faaf96', '1.4.0-beta-16994f59', '1.4.0-beta-b590d23b',
    '1.4.0', '1.4.0-2', '1.4.0-3', '1.4.0-4', '1.4.0-5', '1.4.0-6', '1.4.0-7', '1.4.0-8',
    '1.5.0', '1.5.0-2', '1.5.0-3', '1.5.0-4', '1.5.0-5', '1.5.0-6',
]
OLDEST, NEWEST = VERSIONS[0], VERSIONS[-1]
ANY = 'any'          # the conversion target "every version": the most compatible form

# The first version with each behaviour (source: see FORMATS.md)
EEPROM_START = '1.0.6'        # movies may start from EEPROM (start flag 4)
ST_EXT_SAVESTATE = '1.0.9'    # a snapshot movie also finds NAME.savestate, not only NAME.st
ST_FIX_BIT = '1.0.10'         # states mark a finished SI DMA in bit 31 of the RDRAM manufacturer register
ST_FIX_BIT_LAST = '1.1.9-8'   # ... the last version that sets it on save (1.2.0+ only read it)
ST_EVERY_PREFIX = '1.1.8'     # a.b.c.m64 finds a.st, then a.b.st, then a.b.c.st (before: only a.st)
ST_SCREENSHOT = '1.1.8'       # states may end with a "SCR" screenshot section (option, off by default)
EXT_CHECKED = '1.1.9'         # movies' extended header: version 1 (Wii VC flag); higher versions are REJECTED
ST_SCREENSHOT_RGBA = '1.4.0-beta-b4f8f172'   # the screenshot is 4 bytes a pixel (before: 3)
# The next two are in the source tagged 1.4.0-8 (#926, #927, 2026-08-08/09), but the published 1.4.0-8 binary was
# built before them: tested, it rejects extended-v4 movies and reads LZ4 states as another ROM's, like 1.4.0-7
# (it still has the CLI bug #921 fixed on 08-08, and its config has no st_lz4 / RCP lag keys). Releases rule.
EXT_V4 = '1.5.0'              # extended version 4 (c.eq.s, RDP completion, CPU counter factor, RCP lag)
ST_LZ4 = '1.5.0'              # states may be LZ4 frames (option, ON by default); before only gzip or plain
BUILTIN_PLUGINS = '1.5.0'     # plugins compiled into the exe; movies record them as "TAS Video (built-in)" etc.
EXT_ANY = '1.5.0-3'           # an unknown extended version only gives a warning (1.5.0 .. 1.5.0-2 reject it)
ST_COP0_CLEAN = '1.5.0-6'     # the 8-byte COP0 slots get zero high halves (before: the next register's value)


def vindex(v):
    return VERSIONS.index(v)


def at_least(v, first):
    return vindex(v) >= vindex(first)


def vrange(lo, hi=None):
    """Readable version range: '1.1.9 – 1.4.0-8', '1.5.0 and newer'."""
    if hi is None or hi == NEWEST:
        return f'{lo} and newer' if lo != OLDEST else 'every version'
    if lo == OLDEST:
        return f'{hi} and older'
    return lo if lo == hi else f'{lo} – {hi}'


def prev(v):
    return VERSIONS[vindex(v) - 1]


def ranges_text(ok):
    """[(lo, hi)] from a list of booleans per version."""
    out, start = [], None
    for i, v in enumerate(VERSIONS):
        if ok[i] and start is None:
            start = v
        if not ok[i] and start is not None:
            out.append((start, VERSIONS[i - 1]))
            start = None
    if start is not None:
        out.append((start, NEWEST))
    return out


def describe_ranges(rs):
    if not rs:
        return 'no version'
    return ', '.join(vrange(a, b) for a, b in rs)


class FormatError(Exception):
    """The file isn't a movie / savestate, or is damaged."""


# ---------------------------------------------------------------- movies (.m64)

M64_MAGIC = 0x1A34364D        # 'M64\x1a'
HEADER = 0x400
START_SNAPSHOT, START_POWER_ON, START_EEPROM = 1, 2, 4
EXT_FLAG_NAMES = {0: 'Wii VC emulation', 1: 'accurate c.eq.s (NaN compares)', 2: 'accurate RDP completion'}


# Sync settings a movie can carry: the first version whose movies store it, and the first with a setting for it
SETTING_IN_MOVIE = {'Wii VC emulation': EXT_CHECKED, 'c.eq.s accuracy': EXT_V4, 'RDP completion': EXT_V4,
                    'CPU counter factor': EXT_V4, 'RCP lag factor': EXT_V4}
SETTING_OPTION = {'Wii VC emulation': ('1.1.9', 'Wii VC emulation'),
                  'c.eq.s accuracy': ('1.3.0', 'accurate c.eq.s (NaN)'),
                  'CPU counter factor': (OLDEST, 'Counter Factor'),
                  'RDP completion': (EXT_V4, None), 'RCP lag factor': (EXT_V4, None)}


def setting_advice(name, value, v):
    """What to tell the user about a sync setting the movie was recorded with, for version v (or ANY)."""
    since, option = SETTING_OPTION[name]
    if v != ANY and at_least(v, since) and option:
        return f'recorded with {name} = {value}: the old movie format can\'t store that, so set "{option}" to ' \
               f'{value} in Mupen {v} before playing, or it may desync'
    if v == ANY:
        return f'recorded with {name} = {value}: older versions can\'t read that from the movie; set it by hand ' \
               'where the version has the option, or the movie may desync'
    return f'recorded with {name} = {value}, which Mupen {v} can\'t emulate (added in {since}); it may desync there'


class MovieInfo:
    kind = 'movie'

    def __init__(self, path, data):
        self.path, self.data = path, bytes(data)
        if len(data) < 0x200:
            raise FormatError('too short to be a movie (no 512-byte header)')
        magic, self.version, self.uid, self.length_vis, self.rerecords = struct.unpack_from('<5I', data, 0)
        if magic != M64_MAGIC:
            raise FormatError('not an .m64 movie (no "M64" signature)')
        if not 1 <= self.version <= 3:
            raise FormatError(f'unknown movie version {self.version} (every Mupen reads 1 to 3)')
        if self.version == 3 and len(data) < HEADER:
            raise FormatError('version-3 movie shorter than its 1024-byte header')
        self.vis_per_second, self.controllers, self.ext_version, self.ext_flags = struct.unpack_from('<4B', data, 0x14)
        self.length_samples, self.start_flags, _, self.controller_flags = struct.unpack_from('<IHHI', data, 0x18)
        self.authorship = data[0x24:0x28]
        self.bruteforce, self.rerecords_high = struct.unpack_from('<II', data, 0x28)
        self.cpu_cf, self.rcp_lag = struct.unpack_from('<dd', data, 0x30)
        self.rom_name = data[0xC4:0xE4].split(b'\0')[0].decode('latin-1').strip()
        self.rom_crc, self.rom_country = struct.unpack_from('<IH', data, 0xE4)
        hdr = HEADER if self.version == 3 else 0x200
        self.samples_in_file = (len(data) - hdr) // 4
        self.author = data[0x222:0x300].split(b'\0')[0].decode('utf-8', 'replace') if self.version == 3 else ''
        self.description = data[0x300:0x400].split(b'\0')[0].decode('utf-8', 'replace') if self.version == 3 else ''
        self.plugins = [data[o:o + 64].split(b'\0')[0].decode('latin-1') for o in (0x122, 0x162, 0x1A2, 0x1E2)]

    # -- what the header asks for
    def start_text(self):
        f = self.start_flags
        if f & START_SNAPSHOT:
            return 'from a savestate (needs its .st)'
        if f & START_EEPROM:
            return 'from power-on with the saved EEPROM'
        if f & START_POWER_ON:
            return 'from power-on'
        return f'unknown start flags {f}'

    def sync_settings(self):
        """[(setting, value)] the movie says it was recorded with, when that differs from the old defaults."""
        out = []
        e = self.ext_version
        if e >= 1 and self.ext_flags & 1:
            out.append(('Wii VC emulation', 'on'))
        if e >= 2 and self.ext_flags & 2:
            out.append(('c.eq.s accuracy', 'accurate'))
        if e >= 3 and self.ext_flags & 4:
            out.append(('RDP completion', 'accurate'))
        if e >= 4 and self.cpu_cf != 1.0:
            out.append(('CPU counter factor', f'{self.cpu_cf:g}'))
        if e >= 4 and self.rcp_lag != 0.0:
            out.append(('RCP lag factor', f'{self.rcp_lag:g}'))
        return out

    # -- who reads it
    def problems_in(self, v):
        """Reasons version v can't open this movie (empty: it opens)."""
        p = []
        if self.start_flags & START_EEPROM and not at_least(v, EEPROM_START):
            p.append(f'starts from EEPROM, which Mupen added in {EEPROM_START}')
        e = self.ext_version
        if at_least(v, EXT_CHECKED) and not at_least(v, EXT_V4) and e > 1:
            p.append(f'extended header version {e}: {vrange(EXT_CHECKED, prev(EXT_V4))} only accept 0 or 1 '
                     '("invalid extended version")')
        if at_least(v, EXT_V4) and not at_least(v, EXT_ANY) and e > 4:
            p.append(f'extended header version {e}: {vrange(EXT_V4, prev(EXT_ANY))} only accept 0 to 4')
        return p

    def warnings_in(self, v):
        """Things version v will warn about or may play differently (the movie still opens)."""
        w = []
        if self.version == 3 and self.length_samples > self.samples_in_file:
            w.append(f'the file is cut off: the header says {self.length_samples:,} inputs but it holds '
                     f'{self.samples_in_file:,}')
        for name, value in self.sync_settings():
            if not at_least(v, SETTING_IN_MOVIE[name]):
                w.append(setting_advice(name, value, v))
        if at_least(v, EXT_CHECKED) and self.ext_version == 0 and self.ext_flags:
            w.append('old-format movie with non-zero data in the extended flags byte (Mupen shows a warning)')
        return w

    def builtin_plugins(self):
        """1.5.0+ has its plugins inside the exe and records them as "... (built-in)"."""
        return any(p.endswith('(built-in)') for p in self.plugins)

    def made_by(self):
        e = self.ext_version
        tag = self.authorship.decode('latin-1') if self.authorship.isascii() else ''
        if self.builtin_plugins() and e < 4:
            return f'Mupen {vrange(BUILTIN_PLUGINS)} (built-in plugins); its header was lowered to extended ' \
                   f'version {e} afterwards (a converted or tool-made copy)'
        if e == 0:
            if self.authorship == b'MUPN':
                return 'Mupen 1.1.9 or newer with "Extended movie format" turned off'
            return f'Mupen {vrange(OLDEST, prev(EXT_CHECKED))}, or a newer one with "Extended movie format" off, ' \
                   'or another tool'
        if e == 1:
            return f'Mupen {vrange(EXT_CHECKED, prev(EXT_V4))}' + (f' (tag "{tag}")' if tag else '')
        if e in (2, 3):
            return f'a Mupen development build between 1.4.0-7 and 1.5.0 (extended version {e})'
        if e == 4:
            return f'Mupen {vrange(EXT_V4)}'
        return f'a Mupen newer than {NEWEST} (extended version {e})'

    def opens_in(self):
        return ranges_text([not self.problems_in(v) for v in VERSIONS])


def _movie_out(info, target):
    """The movie bytes for `target` (a version or ANY), and the notes. None if nothing needs changing."""
    d = bytearray(info.data)
    notes = []
    if info.start_flags & START_EEPROM and target != ANY and not at_least(target, EEPROM_START):
        raise FormatError(f'this movie starts from EEPROM, which no Mupen before {EEPROM_START} can play')
    e = info.ext_version
    want = e
    if target == ANY or (at_least(target, EXT_CHECKED) and not at_least(target, EXT_V4)):
        want = min(e, 1)                      # read by every version (older ones ignore these bytes)
    elif at_least(target, EXT_V4) and not at_least(target, EXT_ANY):
        want = min(e, 4)
    if want != e:
        d[0x16] = want
        keep = {0: 0, 1: 0x01, 4: 0x07}[want]
        d[0x17] &= keep
        if want < 4:
            d[0x30:0x44] = bytes(0x14)        # the v4 doubles become the v1 "unused" fields: zero them
        if want == 0:
            d[0x24:0x30] = bytes(12)          # authorship tag, bruteforce data, rerecord high word
        notes.append(f'extended header version {e} → {want}')
        for name, value in info.sync_settings():
            if want < (1 if name == 'Wii VC emulation' else 4):
                notes.append(setting_advice(name, value, target))
    if info.version == 3 and info.length_samples > info.samples_in_file:
        struct.pack_into('<I', d, 0x18, info.samples_in_file)
        notes.append(f'THE FILE IS CUT OFF: its header says {info.length_samples:,} inputs but it holds '
                     f'{info.samples_in_file:,}. The count is set to {info.samples_in_file:,} (older versions '
                     'would play garbage past the end); the missing inputs can\'t be recovered')
    return (bytes(d) if notes else None), notes


# ---------------------------------------------------------------- savestates (.st)

ST_STATIC = 0xA02BB4          # the fixed-size part: ROM MD5 (32) + registers + RDRAM 8 MB + SP mem + TLB tables ...
RDRAM_OFF = 0x1B0             # RDRAM inside the state (little-endian words)
RDRAM_MANUF_OFF = 0x20 + 0x24 # rdram_register.rdram_device_manuf (10th word)
COP0_OFF = 0xA0230C           # 32 slots of 8 bytes; Mupen reads the low 4 of each
SCREEN_MAGIC = b'SCR\0'
GZIP, LZ4, PLAIN = 'gzip', 'LZ4', 'uncompressed'


def decompress_state(raw):
    """(contents, compression) of a savestate file's bytes."""
    if raw[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(raw), GZIP
        except Exception as ex:
            raise FormatError(f'damaged gzip data ({ex})')
    if raw[:4] == b'\x04\x22\x4d\x18':
        try:
            return lz4_frame_decompress(raw), LZ4
        except Exception as ex:
            raise FormatError(f'damaged LZ4 data ({ex})')
    return bytes(raw), PLAIN


class StateInfo:
    kind = 'state'

    def __init__(self, path, raw):
        self.path, self.raw = path, bytes(raw)
        self.data, self.compression = decompress_state(self.raw)
        d = self.data
        if len(d) < ST_STATIC + 8:
            raise FormatError(f'too short for a Mupen savestate ({len(d):,} bytes; at least {ST_STATIC + 8:,})')
        md5 = d[:32]
        if not all(c in b'0123456789ABCDEFabcdef' for c in md5):
            raise FormatError('not a Mupen savestate (it doesn\'t start with a ROM MD5)')
        self.rom_md5 = md5.decode().upper()
        # interrupt queue: (type, count) pairs ending with 0xFFFFFFFF, at most 1 KB
        p = ST_STATIC
        self.events = []
        while True:
            if p + 4 > len(d) or p - ST_STATIC >= 1024:
                raise FormatError('damaged savestate: the interrupt queue has no end marker')
            t = struct.unpack_from('<I', d, p)[0]
            p += 4
            if t == 0xFFFFFFFF:
                break
            if p + 4 > len(d):
                raise FormatError('damaged savestate: cut off in the interrupt queue')
            self.events.append((t, struct.unpack_from('<I', d, p)[0]))
            p += 4
        self.queue_end = p
        if p + 4 > len(d):
            raise FormatError('damaged savestate: cut off after the interrupt queue')
        self.movie = None
        is_movie = struct.unpack_from('<I', d, p)[0]
        p += 4
        if is_movie:
            if p + 20 > len(d):
                raise FormatError('damaged savestate: cut off in its movie block')
            size, uid, cur, vi, length = struct.unpack_from('<5I', d, p)
            self.movie = {'uid': uid, 'sample': cur, 'vi': vi, 'length': length}
            p += 20 + 4 * (length + 1)
            if p > len(d):
                raise FormatError('damaged savestate: its movie block is cut off')
        self.body_end = p                     # everything before this is read by every version
        self.screenshot = None                # (width, height, bytes per pixel)
        rest = d[p:]
        if rest[:4] == SCREEN_MAGIC and len(rest) >= 12:
            w, h = struct.unpack_from('<ii', rest, 4)
            n = len(rest) - 12
            bpp = 4 if w > 0 and h > 0 and n == w * h * 4 else 3 if w > 0 and h > 0 and n == w * h * 3 else None
            self.screenshot = (w, h, bpp)
        self.trailing = len(rest)
        self.fix_bit = bool(struct.unpack_from('<I', d, RDRAM_MANUF_OFF)[0] & 0x80000000)
        lo = struct.unpack_from('<64I', d, COP0_OFF)
        self.cop0_clean = all(lo[2 * i + 1] == 0 for i in range(32))
        self.cop0_spill = all(lo[2 * i + 1] == lo[2 * i + 2] for i in range(31))

    def global_timer(self):
        """SM64 (US layout) gGlobalTimer, for the tests; any game's state has some value there."""
        return struct.unpack_from('<I', self.data, RDRAM_OFF + (0x8032D5D4 & 0x7FFFFF))[0]

    def problems_in(self, v):
        p = []
        if self.compression == LZ4 and not at_least(v, ST_LZ4):
            p.append(f'LZ4-compressed (Mupen added LZ4 states in {ST_LZ4}; older ones read it as the wrong ROM)')
        return p

    def warnings_in(self, v):
        w = []
        if self.screenshot:
            bpp = self.screenshot[2]
            if not at_least(v, ST_SCREENSHOT):
                pass                          # ignored: the loader stops after the movie block
            elif bpp == 4 and not at_least(v, ST_SCREENSHOT_RGBA):
                w.append('has a 4-byte-per-pixel screenshot, which this version reads as 3 bytes a pixel (the state '
                         'still loads; only a paused picture can look wrong)')
            elif bpp == 3 and at_least(v, ST_SCREENSHOT_RGBA):
                w.append('has a 3-byte-per-pixel screenshot, which this version skips')
        if self.fix_bit and not at_least(v, ST_FIX_BIT):
            w.append('saved during an input poll by 1.0.10 – 1.1.9-8; versions before 1.0.10 may run that poll twice')
        return w

    def made_by(self):
        """The versions that write a state like this one (narrowed by every clue in it)."""
        lo, hi = OLDEST, NEWEST
        if self.compression == LZ4:
            lo = ST_LZ4
        if self.screenshot and self.screenshot[2] == 4:
            lo = max(lo, ST_SCREENSHOT_RGBA, key=vindex)
        elif self.screenshot and self.screenshot[2] == 3:
            lo, hi = max(lo, ST_SCREENSHOT, key=vindex), prev(ST_SCREENSHOT_RGBA)
        if self.fix_bit:
            lo, hi = max(lo, ST_FIX_BIT, key=vindex), min(hi, ST_FIX_BIT_LAST, key=vindex)
        if self.cop0_clean and not self.cop0_spill:
            lo = max(lo, ST_COP0_CLEAN, key=vindex)
        elif self.cop0_spill and not self.cop0_clean:
            hi = min(hi, prev(ST_COP0_CLEAN), key=vindex)
        if vindex(lo) > vindex(hi):
            return 'an unknown tool (its clues contradict each other)'
        text = 'any Mupen version' if (lo, hi) == (OLDEST, NEWEST) else f'Mupen {vrange(lo, hi)}'
        if self.compression != LZ4 and at_least(hi, ST_LZ4):
            text += ' (gzip: older versions, or newer ones with LZ4 turned off)' if vindex(lo) < vindex(ST_LZ4) \
                else ' (with LZ4 compression turned off)'
        return text

    def opens_in(self):
        return ranges_text([not self.problems_in(v) for v in VERSIONS])


def _state_out(info, target):
    d = info.data
    notes = []
    old_target = target == ANY or not at_least(target, ST_LZ4)
    body = bytearray(d[:info.body_end])
    tail = d[info.body_end:]
    if info.screenshot:
        bpp = info.screenshot[2]
        if target == ANY or (bpp == 4 and not at_least(target, ST_SCREENSHOT_RGBA)) or \
                (bpp == 3 and at_least(target, ST_SCREENSHOT_RGBA)) or bpp is None:
            tail = b''
            notes.append('screenshot section removed (the target version would misread it; the picture comes back '
                         'on the next frame)')
    elif tail:
        tail = b''
        notes.append(f'{info.trailing} unknown bytes after the state removed')
    if info.fix_bit and target != ANY and not at_least(target, ST_FIX_BIT):
        struct.pack_into('<I', body, RDRAM_MANUF_OFF, struct.unpack_from('<I', body, RDRAM_MANUF_OFF)[0] & 0x7FFFFFFF)
        notes.append('cleared the 1.0.10+ input-poll marker (bit 31 of the RDRAM manufacturer register)')
    compress = info.compression
    if compress == LZ4 and old_target:
        compress = GZIP
        notes.insert(0, 'LZ4 → gzip compression (every version reads gzip)')
    if not notes:
        return None, []
    out = bytes(body) + tail
    if compress == LZ4:
        raise FormatError('internal: LZ4 output is never needed')   # newer versions read gzip too
    if compress == GZIP:
        out = gzip.compress(out, 6, mtime=0)
    return out, notes


# ---------------------------------------------------------------- public API

def analyze_bytes(path, data):
    """MovieInfo or StateInfo for a file's bytes; the kind comes from the contents, not the extension."""
    if data[:4] == struct.pack('<I', M64_MAGIC):
        return MovieInfo(path, data)
    try:
        return StateInfo(path, data)
    except FormatError as ex:
        if path.lower().endswith('.m64'):
            raise FormatError('not an .m64 movie (no "M64" signature)')
        if not path.lower().endswith(('.st', '.savestate')) and data[:2] != b'\x1f\x8b' and \
                data[:4] != b'\x04\x22\x4d\x18':
            raise FormatError('not a Mupen movie or savestate')
        raise ex


def analyze(path):
    with open(path, 'rb') as f:
        data = f.read()
    if not data:
        raise FormatError('empty file')
    return analyze_bytes(path, data)


def convert(info, target):
    """(new bytes or None, notes) for `target`: a version from VERSIONS or ANY."""
    if target != ANY and target not in VERSIONS:
        raise ValueError(f'unknown Mupen version {target!r}')
    return (_movie_out if info.kind == 'movie' else _state_out)(info, target)


def hard_limits(info):
    """Versions no conversion can serve, with the reason: [(first version that can, reason)]."""
    if info.kind == 'movie' and info.start_flags & START_EEPROM:
        return [(EEPROM_START, f'Mupen before {EEPROM_START} can\'t play movies that start from EEPROM '
                               '(no conversion can change that)')]
    return []


def target_versions(info, target):
    """The versions a target stands for: one version, or for ANY every version the file can be made to work in."""
    if target != ANY:
        return [target]
    first = max([OLDEST] + [v for v, _ in hard_limits(info)], key=vindex)
    return VERSIONS[vindex(first):]


def target_label(target):
    return 'every version' if target == ANY else f'Mupen {target}'


# ---------------------------------------------------------------- movie ↔ savestate pairing

def state_candidates(movie_path, version=NEWEST):
    """The savestate names `version` tries for a snapshot movie, in order (same folder as the movie)."""
    folder, name = os.path.split(movie_path)
    stem = name[:-4] if name.lower().endswith('.m64') else os.path.splitext(name)[0]
    exts = ['.st', '.savestate'] if at_least(version, ST_EXT_SAVESTATE) else ['.st']
    if at_least(version, ST_EVERY_PREFIX):
        parts = stem.split('.')
        prefixes = ['.'.join(parts[:i]) for i in range(1, len(parts) + 1)]
    else:
        prefixes = [stem.split('.')[0]]
    return [os.path.join(folder, p + e) for p in prefixes for e in exts]


def find_state(movie_path, version=NEWEST):
    for c in state_candidates(movie_path, version):
        if os.path.isfile(c):
            return c
    return None
