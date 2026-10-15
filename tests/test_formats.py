"""Unit tests for the format rules and the batch conversion (synthetic files; no emulator).

    python -m unittest tests/test_formats.py -v      (from projects/mupen_compat)
"""
import gzip
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from mupencompat import formats as F  # noqa: E402
from mupencompat.convert import Job, add_pairs, run_jobs  # noqa: E402

MD5 = b'C4D59582A41C5CA414C4C05645AA19EC'


def movie(ext=4, flags=0, start=F.START_SNAPSHOT, n=10, length=None, cpu_cf=1.0, rcp=0.0, tag=b'MUPN'):
    h = bytearray(F.HEADER)
    struct.pack_into('<5I', h, 0, F.M64_MAGIC, 3, 1234, n * 2, 7)
    struct.pack_into('<4B', h, 0x14, 60, 1, ext, flags)
    struct.pack_into('<IHHI', h, 0x18, n if length is None else length, start, 0, 1)
    h[0x24:0x28] = tag
    struct.pack_into('<II', h, 0x28, 0, 0)
    struct.pack_into('<dd', h, 0x30, cpu_cf, rcp)
    h[0xC4:0xC4 + 15] = b'SM64 ISLAND 1.1'
    return bytes(h) + bytes(range(4 * n))


def state_body(fix_bit=False, movie_len=None, cop0='spill'):
    d = bytearray(F.ST_STATIC)
    d[:32] = MD5
    if fix_bit:
        struct.pack_into('<I', d, F.RDRAM_MANUF_OFF, 0x80000000 | 0x12)
    for i in range(32):
        struct.pack_into('<I', d, F.COP0_OFF + 8 * i, 0x1000 + i)
        hi = (0x1000 + i + 1 if i < 31 else 0x55) if cop0 == 'spill' else 0
        struct.pack_into('<I', d, F.COP0_OFF + 8 * i + 4, hi)
    d += struct.pack('<4I', 1, 100, 8, 200) + struct.pack('<I', 0xFFFFFFFF)
    if movie_len is None:
        d += struct.pack('<I', 0)
    else:
        d += struct.pack('<I', 1) + struct.pack('<5I', 16 + 4 * (movie_len + 1), 99, 3, 6, movie_len)
        d += bytes(4 * (movie_len + 1))
    return bytes(d)


def screenshot(w=4, h=3, bpp=4):
    return b'SCR\0' + struct.pack('<ii', w, h) + bytes(range(256)) * 0 + bytes(w * h * bpp)


def lz4_stored(data):
    """An LZ4 frame holding the data in uncompressed blocks (valid LZ4; what matters is the frame format)."""
    out = bytearray(b'\x04\x22\x4d\x18' + bytes([0x40, 0x70, 0xDF]))     # FLG v1 independent, BD 4 MB, HC
    for i in range(0, len(data), 4 << 20):
        chunk = data[i:i + (4 << 20)]
        out += struct.pack('<I', len(chunk) | 0x80000000) + chunk
    return bytes(out + struct.pack('<I', 0))


class Movies(unittest.TestCase):
    def test_v4_to_old_versions(self):
        m = F.MovieInfo('a.m64', movie(ext=4, flags=0b111, cpu_cf=2.0, rcp=1.0))
        self.assertTrue(m.problems_in('1.3.0-11'))
        self.assertFalse(m.problems_in('1.1.8-2'))          # before 1.1.9 the extended header is ignored
        self.assertFalse(m.problems_in('1.5.0'))
        self.assertTrue(m.problems_in('1.4.0-8'))      # the published 1.4.0-8 binary predates v4
        data, notes = F.convert(m, '1.3.0-11')
        n = F.MovieInfo('a.m64', data)
        self.assertEqual((n.ext_version, n.ext_flags), (1, 1))  # Wii VC kept, v2/v3 flags dropped
        self.assertEqual(data[0x30:0x44], bytes(0x14))
        self.assertEqual(data[F.HEADER:], m.data[F.HEADER:])    # inputs untouched
        changed = [i for i in range(F.HEADER) if data[i] != m.data[i]]
        self.assertTrue(all(i in (0x16, 0x17) or 0x30 <= i < 0x44 for i in changed), changed)
        self.assertFalse(n.problems_in('1.3.0-11'))
        self.assertTrue(any('RCP lag' in x for x in notes) and any('Counter Factor' in x for x in notes))
        self.assertTrue(any('c.eq.s' in x for x in notes))

    def test_no_change_where_it_already_opens(self):
        m = F.MovieInfo('a.m64', movie(ext=4))
        for v in ('1.1.8-2', '1.5.0', F.NEWEST):
            self.assertIsNone(F.convert(m, v)[0], v)
        self.assertIsNotNone(F.convert(m, F.ANY)[0])
        self.assertIsNone(F.convert(F.MovieInfo('b.m64', movie(ext=1)), F.ANY)[0])

    def test_future_extended_version(self):
        m = F.MovieInfo('a.m64', movie(ext=5))
        self.assertTrue(m.problems_in('1.5.0-2'))
        self.assertFalse(m.problems_in('1.5.0-3'))
        self.assertEqual(F.MovieInfo('a', F.convert(m, '1.5.0-2')[0]).ext_version, 4)

    def test_eeprom_start_needs_106(self):
        m = F.MovieInfo('a.m64', movie(ext=0, start=F.START_EEPROM))
        self.assertTrue(m.problems_in('1.0.5'))
        self.assertFalse(m.problems_in('1.0.6'))
        with self.assertRaises(F.FormatError):
            F.convert(m, '1.0.5')

    def test_every_version_ignores_the_eeprom_limit(self):
        from mupencompat import report
        m = F.MovieInfo('a.m64', movie(ext=1, start=F.START_EEPROM))
        self.assertEqual(report.status_for(m, F.ANY)[0], 'ok')
        self.assertEqual(report.status_for(m, '1.0.5')[0], 'error')
        self.assertIn('EEPROM', report.describe(m, F.ANY).split('For every version:')[1])

    def test_length_clamped(self):
        m = F.MovieInfo('a.m64', movie(ext=1, n=10, length=50))
        data, notes = F.convert(m, '1.3.0-11')
        self.assertEqual(F.MovieInfo('a', data).length_samples, 10)

    def test_v0_from_ext_off_build(self):
        self.assertIn('Extended movie format', F.MovieInfo('a', movie(ext=0, tag=b'MUPN')).made_by())

    def test_bad_movies(self):
        with self.assertRaises(F.FormatError):
            F.MovieInfo('a', b'XXXX' + bytes(2000))
        with self.assertRaises(F.FormatError):
            F.MovieInfo('a', movie()[:600])


class States(unittest.TestCase):
    def test_lz4_to_gzip(self):
        body = state_body()
        s = F.StateInfo('a.st', lz4_stored(body))
        self.assertEqual(s.compression, F.LZ4)
        self.assertTrue(s.problems_in('1.4.0-8'))
        self.assertFalse(s.problems_in('1.5.0'))
        data, notes = F.convert(s, '1.3.0-11')
        self.assertEqual(data[:2], b'\x1f\x8b')
        self.assertEqual(gzip.decompress(data), body)
        self.assertIsNone(F.convert(s, '1.5.0')[0])
        self.assertIn('1.5.0', s.made_by())

    def test_screenshots(self):
        body = state_body()
        rgba = F.StateInfo('a.st', gzip.compress(body + screenshot(bpp=4)))
        self.assertEqual(rgba.screenshot, (4, 3, 4))
        self.assertTrue(rgba.warnings_in('1.3.0-11'))
        data, _ = F.convert(rgba, '1.3.0-11')
        self.assertEqual(gzip.decompress(data), body)
        self.assertIsNone(F.convert(rgba, '1.4.0-8')[0])
        rgb = F.StateInfo('a.st', gzip.compress(body + screenshot(bpp=3)))
        self.assertEqual(rgb.screenshot, (4, 3, 3))
        self.assertIsNone(F.convert(rgb, '1.3.0-11')[0])
        self.assertEqual(gzip.decompress(F.convert(rgb, '1.5.0-5')[0]), body)
        self.assertIn('1.1.8', rgb.made_by())

    def test_fix_bit(self):
        s = F.StateInfo('a.st', gzip.compress(state_body(fix_bit=True)))
        self.assertTrue(s.fix_bit)
        self.assertIn('1.0.10', s.made_by())
        self.assertIsNone(F.convert(s, '1.0.10')[0])
        out = F.StateInfo('a.st', F.convert(s, '1.0.9')[0])
        self.assertFalse(out.fix_bit)
        self.assertEqual(struct.unpack_from('<I', out.data, F.RDRAM_MANUF_OFF)[0], 0x12)

    def test_movie_block_and_cop0(self):
        s = F.StateInfo('a.st', state_body(movie_len=5, cop0='clean'))
        self.assertEqual(s.movie, {'uid': 99, 'sample': 3, 'vi': 6, 'length': 5})
        self.assertEqual(s.compression, F.PLAIN)
        self.assertIn('1.5.0-6', s.made_by())
        self.assertIn('1.5.0-5', F.StateInfo('b', lz4_stored(state_body())).made_by())

    def test_damaged(self):
        with self.assertRaises(F.FormatError):
            F.StateInfo('a.st', gzip.compress(state_body())[:5000])
        with self.assertRaises(F.FormatError):
            F.StateInfo('a.st', b'hello' * 10)
        bad_queue = bytearray(state_body())
        bad_queue[F.ST_STATIC + 16:F.ST_STATIC + 20] = b'\0\0\0\0'      # no end marker
        with self.assertRaises(F.FormatError):
            F.StateInfo('a.st', bytes(bad_queue[:F.ST_STATIC + 20]))


class Pairing(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='mupencompat_')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def put(self, name, data):
        p = os.path.join(self.d, name)
        open(p, 'wb').write(data)
        return p

    def test_candidates(self):
        m = os.path.join(self.d, 'a.b.c.m64')
        names = lambda v: [os.path.basename(x) for x in F.state_candidates(m, v)]  # noqa: E731
        self.assertEqual(names('1.0.8'), ['a.st'])
        self.assertEqual(names('1.1.7'), ['a.st', 'a.savestate'])
        self.assertEqual(names('1.1.8'), ['a.st', 'a.savestate', 'a.b.st', 'a.b.savestate', 'a.b.c.st',
                                          'a.b.c.savestate'])

    def test_pair_copy_and_rename_for_old_versions(self):
        m = self.put('run.fast.m64', movie(ext=4))
        s = self.put('run.fast.st', lz4_stored(state_body()))
        jobs = [Job(m)]
        added = add_pairs(jobs)
        self.assertEqual([j.path for j in added], [s])
        out = os.path.join(self.d, 'out')
        res = run_jobs(jobs, '1.1.7', out_dir=out)
        self.assertEqual(sorted(os.listdir(out)), ['run.fast.m64', 'run.st'])     # 1.1.7 only finds run.st
        self.assertEqual([r.status for r in res], ['copied', 'converted'])       # the v4 movie opens in 1.1.7,
        # but its state needed a change, so the movie comes along (the pair stays together)
        res = run_jobs(jobs, '1.3.0-11', out_dir=os.path.join(self.d, 'out2'))
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, 'out2'))), ['run.fast.m64', 'run.fast.st'])
        self.assertEqual([r.status for r in res], ['converted', 'converted'])

    def test_replace_keeps_backup(self):
        s = self.put('x.st', lz4_stored(state_body()))
        before = open(s, 'rb').read()
        res = run_jobs([Job(s)], '1.3.0-11', mode='replace')
        self.assertEqual(res[0].status, 'converted')
        self.assertEqual(open(s + '.bak', 'rb').read(), before)
        self.assertEqual(F.analyze(s).compression, F.GZIP)
        res = run_jobs([Job(s)], '1.3.0-11', mode='replace')                     # nothing left to do
        self.assertEqual(res[0].status, 'unchanged')
        self.assertFalse(os.path.exists(s + ' (2).bak'))

    def test_compatible_pair_not_copied_beside_a_converted_file(self):
        # reported case: an ext-1 movie + gzip state with an unrelated LZ4 state: only the LZ4
        # state may be written
        sub = os.path.join(self.d, 'island')
        os.makedirs(sub)
        m = os.path.join(sub, 'sk_6.reds100c.to_whomp.m64')
        open(m, 'wb').write(movie(ext=1))
        st = os.path.join(sub, 'sk_6.st')
        open(st, 'wb').write(gzip.compress(state_body()))
        other = self.put('my state.st', lz4_stored(state_body()))
        jobs = [Job(m), Job(other)]
        add_pairs(jobs)
        res = run_jobs(jobs, '1.3.0-11')
        self.assertEqual({os.path.basename(r.job.path): r.status for r in res},
                         {'sk_6.reds100c.to_whomp.m64': 'unchanged', 'sk_6.st': 'unchanged',
                          'my state.st': 'converted'})
        self.assertFalse(os.path.exists(os.path.join(sub, 'for Mupen 1.3.0-11')))
        self.assertEqual(os.listdir(os.path.join(self.d, 'for Mupen 1.3.0-11')), ['my state.st'])

    def test_names_next_to_the_originals(self):
        # naming next to the originals: movie-only changes get '.mupen<ver>' appended (it still loads the original
        # state); anything with a state that changes gets the 'mupen<ver>_' prefix, on the movie too
        from mupencompat.convert import appended, name_prefix, prefixed
        cases = {   # folder: (movie ext, state is LZ4, start)
            'movie_only': (4, False, F.START_SNAPSHOT), 'both': (4, True, F.START_SNAPSHOT),
            'state_only': (1, True, F.START_SNAPSHOT), 'neither': (1, False, F.START_SNAPSHOT),
            'poweron': (4, None, F.START_POWER_ON), 'lone_state': (None, True, None)}
        for target in ('1.3.0-11', '1.1.7', F.ANY):
            for case, (ext, lz4, start) in cases.items():
                d = os.path.join(self.d, target.replace('.', '_'), case)
                os.makedirs(d)
                files = []
                if ext is not None:
                    files.append(os.path.join(d, 'sk_6.reds100c.to_whomp.m64'))
                    open(files[-1], 'wb').write(movie(ext=ext, start=start))
                if lz4 is not None:
                    st = os.path.join(d, 'sk_6.st' if ext is not None else 'my state.st')
                    open(st, 'wb').write(lz4_stored(state_body()) if lz4 else gzip.compress(state_body()))
                    if ext is None:
                        files.append(st)
                jobs = [Job(p) for p in files]
                add_pairs(jobs)
                run_jobs(jobs, target, mode='tagged')
                new = sorted(set(os.listdir(d)) - {'sk_6.reds100c.to_whomp.m64', 'sk_6.st', 'my state.st'})
                m, s = 'sk_6.reds100c.to_whomp.m64', 'sk_6.st'
                movie_fine = target == '1.1.7'          # versions before 1.1.9 ignore the extended header
                want = {'movie_only': [] if movie_fine else [appended(m, target)],
                        'both': sorted([prefixed(m, target), prefixed(s, target)]),
                        'state_only': sorted([prefixed(m, target), prefixed(s, target)]), 'neither': [],
                        'poweron': [] if movie_fine else [appended(m, target)],
                        'lone_state': [prefixed('my state.st', target)]}[case]
                self.assertEqual(new, want, (target, case))
                if case in ('both', 'state_only') or (case == 'movie_only' and not movie_fine):
                    # Mupen's own pairing: the new movie loads a state that works in the target
                    tm = os.path.join(d, new[0] if case == 'movie_only' else prefixed(m, target))
                    for v in ([target] if target != F.ANY else ['1.0.0', '1.1.7', '1.3.0-11', F.NEWEST]):
                        found = F.find_state(tm, v)
                        self.assertFalse(F.analyze(found).problems_in(v), (target, case, v, found))
                        if case != 'movie_only':
                            self.assertEqual(os.path.basename(found), prefixed(s, target), (target, case, v))
            self.assertNotIn('.', name_prefix(target))                 # a dot in the prefix would break pairing
        self.assertEqual(appended('sk_6.reds100c.to_whomp.m64', '1.3.0-11'), 'sk_6.reds100c.to_whomp.mupen1.3.0-11.m64')
        self.assertEqual(prefixed('sk_6.st', '1.3.0-11'), 'mupen1_3_0-11_sk_6.st')

    def test_tagged_pair_that_already_works_is_skipped(self):
        m = self.put('run.m64', movie(ext=1))
        self.put('run.st', gzip.compress(state_body()))
        jobs = [Job(m)]
        add_pairs(jobs)
        res = run_jobs(jobs, '1.3.0-11', mode='tagged')
        self.assertEqual([r.status for r in res], ['unchanged', 'unchanged'])
        self.assertEqual(sorted(os.listdir(self.d)), ['run.m64', 'run.st'])

    def test_standalone_compatible_file_not_copied(self):
        s = self.put('ok.st', gzip.compress(state_body()))
        out = os.path.join(self.d, 'o')
        res = run_jobs([Job(s)], '1.3.0-11', out_dir=out)
        self.assertEqual(res[0].status, 'unchanged')
        self.assertFalse(os.path.exists(os.path.join(out, 'ok.st')))

    def test_unreadable_file_reported(self):
        p = self.put('junk.m64', b'not a movie')
        res = run_jobs([Job(p)], F.ANY, out_dir=os.path.join(self.d, 'o'))
        self.assertEqual(res[0].status, 'error')


if __name__ == '__main__':
    unittest.main()
