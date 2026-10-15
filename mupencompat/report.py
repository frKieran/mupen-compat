"""Plain-text reports for one file (used by the GUI's details pane and the command line)."""
from . import formats as F


def status_for(info, target):
    """('ok' | 'convert' | 'warn' | 'error', short text) for converting `info` to `target`."""
    try:
        data, notes = F.convert(info, target)
    except F.FormatError as ex:
        return 'error', str(ex)
    versions = F.target_versions(info, target)
    blockers = [p for v in versions for p in info.problems_in(v)]
    if data is None:
        if blockers:
            return 'error', blockers[0]
        warns = [w for v in versions for w in info.warnings_in(v)]
        return ('warn', warns[0]) if warns else ('ok', 'already works')
    return 'convert', notes[0]


def movie_text(m):
    if not m:
        return 'none (saved with no movie active)'
    if m['length'] == 0:
        return f'saved as a movie recording started (movie uid {m["uid"]})'
    return f'saved during a movie (uid {m["uid"]}), at input {m["sample"]:,} of {m["length"]:,}'


def describe(info, target=None):
    lines = []
    name = info.path
    if info.kind == 'movie':
        lines += [f'Movie: {name}',
                  f'  ROM: "{info.rom_name}"  CRC {info.rom_crc:08X}  country {info.rom_country:#x}',
                  f'  Starts {info.start_text()}',
                  f'  {info.length_samples:,} inputs, {info.length_vis:,} VIs, '
                  f'{info.rerecords + (info.rerecords_high << 32):,} rerecords',
                  f'  Format: movie version {info.version}, extended header version {info.ext_version}']
        if info.author:
            lines.append(f'  Author: {info.author}')
        s = info.sync_settings()
        if s:
            lines.append('  Recorded with: ' + ', '.join(f'{a} = {b}' for a, b in s))
        if any(info.plugins):
            lines.append('  Plugins at recording (a record only; Mupen plays with the ones you select): ' +
                         ' / '.join(p or '?' for p in info.plugins))
    else:
        lines += [f'Savestate: {name}',
                  f'  ROM MD5: {info.rom_md5}',
                  f'  Compression: {info.compression}',
                  '  Movie inside: ' + movie_text(info.movie)]
        if info.screenshot:
            w, h, bpp = info.screenshot
            lines.append(f'  Screenshot: {w}x{h}, {bpp or "?"} bytes a pixel')
    lines.append(f'  Made by: {info.made_by()}')
    lines.append(f'  Opens in: {F.describe_ranges(info.opens_in())}')
    bad = [r for r in F.ranges_text([bool(info.problems_in(v)) for v in F.VERSIONS])]
    if bad:
        lines.append(f'  Does NOT open in: {F.describe_ranges(bad)}')
        first = next(v for v in F.VERSIONS if info.problems_in(v))
        lines += [f'    ({p})' for p in info.problems_in(first)]
    if target:
        lines.append('')
        lines.append(f'For {F.target_label(target)}:')
        if target == F.ANY:
            lines += [f'  Note: {why}' for _, why in F.hard_limits(info)]
        if info.kind == 'movie' and info.builtin_plugins() and (target == F.ANY or
                                                                not F.at_least(target, F.BUILTIN_PLUGINS)):
            lines.append(f'  Note: recorded with 1.5\'s built-in plugins. Older Mupens need their own era\'s plugin '
                         'DLLs (TASVideo, TASInput, TASRSP); for SM64 the movie synced with them in every tested '
                         'version.')
        try:
            data, notes = F.convert(info, target)
        except F.FormatError as ex:
            lines.append(f'  Can\'t convert: {ex}')
            return '\n'.join(lines)
        if data is None:
            versions = F.target_versions(info, target)
            warns = sorted({w for v in versions for w in info.warnings_in(v)})
            lines.append('  Nothing to change: it already works there.')
            lines += [f'  Note: {w}' for w in warns]
        else:
            lines.append('  The converted copy will change:')
            lines += [f'  - {n}' for n in notes]
            after = F.analyze_bytes(info.path, data)
            versions = F.target_versions(info, target)
            left = sorted({p for v in versions for p in after.problems_in(v)})
            lines += [f'  Still a problem: {p}' for p in left]
            lines.append(f'  After converting it opens in: {F.describe_ranges(after.opens_in())}')
    return '\n'.join(lines)
