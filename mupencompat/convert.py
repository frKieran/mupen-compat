"""Converting a batch of files: movie + savestate pairs, copy or replace, output names that the target pairs."""
import os
import shutil

from . import formats as F


class Job:
    def __init__(self, path, auto=False, pair_movie=None):
        self.path = os.path.abspath(path)
        self.auto = auto                       # added because a movie in the batch pairs with it
        self.pair_movie = pair_movie           # a movie this state was paired with by hand
        self.info = self.error = None
        self.read()

    def read(self):
        """(Re)read the file: the batch converts what is on disk when Convert is pressed."""
        info = error = None
        try:
            info = F.analyze(self.path)
        except FileNotFoundError:
            error = 'the file is gone (moved or deleted after it was added)'
        except (OSError, F.FormatError) as ex:
            error = str(ex)
        except Exception as ex:                       # noqa: BLE001 - a damaged file must never crash the app
            error = f'unreadable ({type(ex).__name__}: {ex})'
        self.info, self.error = info, error

    @property
    def kind(self):
        return self.info.kind if self.info else '?'


class Result:
    def __init__(self, job, status, notes=(), out=None):
        self.job, self.status, self.notes, self.out = job, status, list(notes), out

    def text(self):
        head = {'converted': 'converted', 'copied': 'copied with its pair', 'renamed': 'written under a new name',
                'unchanged': 'already works there, skipped (no copy made)', 'error': 'FAILED'}[self.status]
        s = f'{os.path.basename(self.job.path)}: {head}'
        if self.out and self.out != self.job.path:
            s += f' -> {self.out}'
        return s + ''.join(f'\n  - {n}' for n in self.notes)


def output_folder(path, target):
    label = 'every Mupen' if target == F.ANY else f'Mupen {target}'
    return os.path.join(os.path.dirname(path), f'for {label}')


def add_pairs(jobs):
    """For each snapshot movie, add the savestate Mupen would load for it, unless it's in the batch already.
    Returns the new jobs."""
    have = {os.path.normcase(j.path) for j in jobs}
    added = []
    for j in list(jobs):
        if j.info and j.kind == 'movie' and j.info.start_flags & F.START_SNAPSHOT:
            st = pair_in_batch(j, jobs) or F.find_state(j.path)
            if st and os.path.normcase(os.path.abspath(st)) not in have:
                nj = Job(st, auto=True)
                jobs.append(nj)
                added.append(nj)
                have.add(os.path.normcase(nj.path))
    return added


def pair_in_batch(movie_job, jobs):
    """The batch's savestate this movie loads in the newest Mupen (same folder, Mupen's name rule)."""
    for j in jobs:
        if j.pair_movie and os.path.normcase(j.pair_movie) == os.path.normcase(movie_job.path):
            return j.path
    states = {os.path.normcase(j.path): j.path for j in jobs if j.info and j.kind == 'state'}
    for c in F.state_candidates(movie_job.path):
        if os.path.normcase(c) in states:
            return states[os.path.normcase(c)]
    return None


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f'{base} ({i}){ext}'):
        i += 1
    return f'{base} ({i}){ext}'


def _write(path, data):
    tmp = path + '.tmp-mupencompat'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _os_error(ex, path):
    if isinstance(ex, PermissionError):
        return f'cannot write {path}: access denied (read-only file, or open in another program?)'
    return f'cannot write {path}: {ex.strerror or ex}'


def name_prefix(target):
    """The prefix for files written next to the originals when a savestate is involved: no dots, because Mupen pairs a
    movie with a state by the dotted parts of the movie's name, shortest first."""
    return 'mupen_all_' if target == F.ANY else 'mupen' + target.replace('.', '_') + '_'


def name_suffix(target):
    """The '.mupen<version>' appended before the extension: for a movie that needs a change while its savestate (if
    any) already works. The movie's first dotted part stays the same, so it still loads the original state."""
    return '.mupen_all' if target == F.ANY else '.mupen' + target


def prefixed(name, target):
    """my state.st -> mupen1_3_0-11_my state.st"""
    return name_prefix(target) + name


def appended(name, target):
    """run_6.redcoins.to_whomp.m64 -> run_6.redcoins.to_whomp.mupen1.3.0-11.m64"""
    stem, ext = os.path.splitext(name)
    return f'{stem}{name_suffix(target)}{ext}'


def run_jobs(jobs, target, mode='copy', out_dir=None, backup=True, overwrite=True):
    """Convert every job for `target`. Modes:
      'copy'    write into out_dir (default: a "for Mupen X" folder beside each file), same names;
      'tagged'  write next to each original under a new name:
                  a movie that needs a change while its state works (or a power-on movie): name.mupen<ver>.m64,
                    which still loads the original state; the state isn't copied;
                  a state that needs a change (lone, or with a movie that may or may not need one): mupen<ver>_name,
                    and its movie gets the same prefix so that it loads the converted state;
      'replace' overwrite the originals (NAME.bak kept when backup is set).
    Only files that need a change are written. In 'copy', a movie and the state it loads stay together: when either
    needs a change both are written, when both already work neither is. Every file is read again first, and each
    succeeds or fails on its own. Returns a Result per job, in order."""
    jobs = [Job(j.path, auto=j.auto, pair_movie=j.pair_movie) for j in jobs]     # the window's jobs stay untouched
    pairs = {}                     # state path -> its movie job
    for j in jobs:
        if j.info and j.kind == 'movie' and j.info.start_flags & F.START_SNAPSHOT:
            st = pair_in_batch(j, jobs)
            if st:
                pairs[os.path.normcase(st)] = j
    partner = {}
    for st_key, movie in pairs.items():
        st_job = next(j for j in jobs if os.path.normcase(j.path) == st_key)
        partner[id(st_job)], partner[id(movie)] = movie, st_job
    # pass 1: what each file needs
    plan = {}
    for j in jobs:
        if j.error:
            plan[id(j)] = {'error': [j.error]}
            continue
        try:
            data, notes = F.convert(j.info, target)
        except F.FormatError as ex:
            plan[id(j)] = {'error': [str(ex)]}
            continue
        name, extra = os.path.basename(j.path), []
        movie = pairs.get(os.path.normcase(j.path))
        if mode != 'tagged' and movie is not None:
            name, note = _state_name(j, movie, target)
            extra += [note] if note else []
        renamed = mode != 'tagged' and name != os.path.basename(j.path)
        plan[id(j)] = {'data': data, 'notes': notes, 'extra': extra, 'name': name,
                       'needs': data is not None or renamed}
    # pass 2: names and what gets written
    for j in jobs:
        p = plan[id(j)]
        if 'error' in p:
            continue
        mate = partner.get(id(j))
        mp = plan.get(id(mate)) if mate is not None else None
        mate_needs = bool(mp and 'error' not in mp and mp['needs'])
        p['why'] = None
        if mode == 'tagged':
            if j.kind == 'state':
                st_needs = p['needs']
            else:
                st_needs = mate_needs
            if j.kind == 'movie' and not st_needs:
                p['name'] = appended(os.path.basename(j.path), target)        # still loads the original state
                p['write'] = p['needs']
            elif j.kind == 'movie':
                p['name'] = prefixed(os.path.basename(j.path), target)        # loads the prefixed state
                p['write'] = True
                if not p['needs']:
                    p['why'] = f'needed no change; written with the prefix so that it loads the converted ' \
                               f'{os.path.basename(mate.path)}'
            elif mate is not None:                                            # a state with its movie
                p['name'] = prefixed(os.path.basename(mate.path), target).split('.')[0] + '.st'
                p['write'] = p['needs']
                if not p['needs'] and mate_needs:
                    p['extra'].append(f'needed no change; {appended(os.path.basename(mate.path), target)} still '
                                      'loads it')
            else:                                                             # a lone state
                p['name'] = prefixed(os.path.basename(j.path), target)
                p['write'] = p['needs']
        elif mode == 'replace':
            p['write'] = p['needs']
        else:
            p['write'] = p['needs'] or mate_needs
            if not p['needs'] and mate_needs:
                p['why'] = f'needed no change; written with {os.path.basename(mate.path)}, which did, so the movie ' \
                           'finds its savestate'
    out = []
    for j in jobs:
        p = plan[id(j)]
        if 'error' in p:
            out.append(Result(j, 'error', p['error']))
            continue
        try:
            out.append(_write_one(j, p, target, mode, out_dir, backup, overwrite))
        except OSError as ex:
            out.append(Result(j, 'error', [_os_error(ex, ex.filename or j.path)]))
    return out


def _state_name(j, movie, target):
    """(name, note): the name the target version finds next to `movie`, if the state's own name isn't one."""
    name = os.path.basename(j.path)
    first = os.path.basename(movie.path)[:-4].split('.')[0] + '.st'
    tv = F.OLDEST if target == F.ANY else target
    found_by = [os.path.basename(c).lower() for c in F.state_candidates(movie.path, tv)]
    if name.lower() in found_by:
        return name, None
    who = 'every version' if target == F.ANY else f'Mupen {target}'
    return first, f'named {first} so that {who} finds it for {os.path.basename(movie.path)}'


def _write_one(j, p, target, mode, out_dir, backup, overwrite):
    data, notes, extra, name = p['data'], p['notes'], p['extra'], p['name']
    raw = j.info.raw if j.kind == 'state' else j.info.data
    if not p['write']:
        return Result(j, 'unchanged', extra, j.path)        # already works (and its pair needs nothing from it)
    if mode == 'replace':
        dest = os.path.join(os.path.dirname(j.path), name)
        if dest != j.path and os.path.exists(dest):
            return Result(j, 'error', [f'{os.path.basename(dest)} already exists there; Mupen would load that one '
                                       'instead. Use "Make new copies", or rename one of them'])
        if dest == j.path and not os.access(j.path, os.W_OK):
            return Result(j, 'error', [_os_error(PermissionError(), j.path)])
        if backup and data is not None:
            shutil.copy2(j.path, _unique(j.path + '.bak'))
        _write(dest, data if data is not None else raw)
        return Result(j, 'converted' if data is not None else 'renamed', notes + extra, dest)
    folder = os.path.dirname(j.path) if mode == 'tagged' else out_dir or output_folder(j.path, target)
    dest = os.path.join(folder, name)
    if os.path.normcase(os.path.abspath(dest)) == os.path.normcase(j.path):
        return Result(j, 'error', ["the output folder is the file's own folder; choose another folder, \"next to "
                                   'each original, with the version in the name", or "Replace"'])
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(dest) and not overwrite:
        dest = _unique(dest)
    _write(dest, data if data is not None else raw)
    if data is not None:
        return Result(j, 'converted', notes + extra, dest)
    if p.get('why'):                   # written only to keep the pair working
        return Result(j, 'copied', extra + [p['why']], dest)
    return Result(j, 'renamed', extra, dest)
