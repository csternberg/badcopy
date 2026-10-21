#!/usr/bin/env python3
"""badcopy - copy files that may reside (partly) on bad blocks.

Readable parts are copied; unreadable parts are filled with zeros so all
offsets stay intact. Files that had unreadable parts get the suffix "_bad"
added to their name (name_bad.ext) and every bad range is written to
badcopy.log in the destination folder.

Usage:
    python badcopy.py (-R | -C) [-S source] -D destination [-P n] [-O]

Only the Python standard library is used (Python 3.8+).
"""
import sys

if sys.version_info < (3, 8):
    sys.exit(
        "badcopy needs Python 3.8 or newer (found %s).\n"
        "Install a current Python from https://www.python.org/downloads/ or, on Windows:\n"
        "    winget install Python.Python.3.12" % sys.version.split()[0]
    )

import argparse
import fnmatch
import os
import re
import shutil
import time

SECTOR = 512                 # smallest unit we give up on
CHUNK = 1024 * 1024          # first-try read size
DEFAULT_PASSES = 3           # retry passes over bad sectors
LOG_NAME = "badcopy.log"
BAD_SUFFIX = "_bad"
WILDCARDS = "*?["
CR = chr(13)                 # carriage return, for in-place progress line


class FatalError(Exception):
    """Error that makes continuing pointless (e.g. destination full)."""


def fmt_size(n):
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d bytes" % n


class Progress:
    """Single-line progress indicator (only shown when stdout is a terminal)."""

    def __init__(self):
        self.enabled = sys.stdout.isatty()
        self.label = ""
        self.last = 0.0

    def start(self, label):
        self.label = label
        self.last = 0.0

    def update(self, done, total, phase=""):
        if not self.enabled:
            return
        now = time.time()
        if done < total and now - self.last < 0.1:
            return
        self.last = now
        pct = 100.0 if not total else done * 100.0 / total
        tail = "  %5.1f%%  %s of %s%s" % (pct, fmt_size(done), fmt_size(total),
                                         "  " + phase if phase else "")
        width = shutil.get_terminal_size((80, 24)).columns - 1
        label = self.label
        room = width - len(tail)
        if len(label) > room:
            label = "..." + label[-(room - 3):] if room > 3 else ""
        sys.stdout.write(CR + (label + tail)[:width].ljust(width))
        sys.stdout.flush()

    def clear(self):
        if self.enabled:
            width = shutil.get_terminal_size((80, 24)).columns - 1
            sys.stdout.write(CR + " " * width + CR)
            sys.stdout.flush()


def read_range(f, offset, size):
    f.seek(offset)
    return f.read(size)


def add_range(ranges, off, length):
    """Append (off, length), merging with the previous range if adjacent."""
    if ranges and ranges[-1][0] + ranges[-1][1] == off:
        ranges[-1] = (ranges[-1][0], ranges[-1][1] + length)
    else:
        ranges.append((off, length))


def retry_bad(fin, fout, bad, passes, progress=None):
    """Re-read bad ranges sector by sector; patch recovered data into fout."""
    total = sum(n for _, n in bad)
    for p in range(passes):
        if not bad:
            break
        still = []
        done = 0
        for off, n in bad:
            for o in range(off, off + n, SECTOR):
                m = min(SECTOR, off + n - o)
                if progress:
                    progress(done, total, "retry %d/%d" % (p + 1, passes))
                done += m
                try:
                    data = read_range(fin, o, m)
                except OSError:
                    data = None
                if data and len(data) == m:
                    try:
                        fout.seek(o)
                        fout.write(data)
                    except OSError as e:
                        raise FatalError("write failed: %s" % e)
                else:
                    add_range(still, o, m)
        bad = still
    return bad


def copy_file(src, tmp_dst, size, passes=0, progress=None):
    """Copy src to tmp_dst. Returns list of (offset, length) bad ranges."""
    bad = []

    def add_bad(off, length):
        add_range(bad, off, length)

    with open(src, "rb", buffering=0) as fin:
        try:
            fout = open(tmp_dst, "wb")
        except OSError as e:
            raise FatalError("cannot create %s: %s" % (tmp_dst, e))
        with fout:
            # stack of (offset, length) still to do; processed in file order
            stack = []
            pos = 0
            done = 0
            while pos < size or stack:
                if not stack:
                    n = min(CHUNK, size - pos)
                    stack.append((pos, n))
                    pos += n
                off, n = stack.pop()
                try:
                    data = read_range(fin, off, n)
                    err = None
                except OSError as e:
                    data, err = None, e
                if data:
                    done += len(data)
                    if progress:
                        progress(done, size)
                    try:
                        fout.write(data)
                    except OSError as e:
                        raise FatalError("write failed on %s: %s" % (tmp_dst, e))
                    if len(data) < n:        # short read: do the rest
                        stack.append((off + len(data), n - len(data)))
                    continue
                # nothing read: error, or file ended early
                if err is not None and n > SECTOR:
                    half = (n // 2 // SECTOR or 1) * SECTOR
                    half = min(half, n - 1)
                    stack.append((off + half, n - half))   # second half
                    stack.append((off, half))              # first half (popped next)
                    continue
                try:
                    fout.write(bytes(n))
                except OSError as e:
                    raise FatalError("write failed on %s: %s" % (tmp_dst, e))
                add_bad(off, n)
                done += n
                if progress:
                    progress(done, size)
            if bad and passes:
                bad = retry_bad(fin, fout, bad, passes, progress)
    return bad


def split_source(source):
    """Return (base_dir, name_pattern) for the -S argument."""
    if source is None:
        return os.getcwd(), "*"
    if os.path.isdir(source):
        return os.path.abspath(source), "*"
    folder, pattern = os.path.split(source)
    if any(c in folder for c in WILDCARDS):
        raise SystemExit("error: wildcards are only allowed in the file-name part of -S")
    folder = os.path.abspath(folder or ".")
    if not os.path.isdir(folder):
        raise SystemExit("error: source folder not found: %s" % folder)
    if pattern in ("*.*", ""):
        pattern = "*"            # *.* means "everything", as in cmd.exe
    return folder, pattern


def iter_files(base, pattern, recursive, skip_dir):
    def match(name):
        return fnmatch.fnmatch(name.lower(), pattern.lower())

    if not recursive:
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isfile(p) and match(name):
                yield p
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if os.path.abspath(os.path.join(root, d)) != skip_dir)
        for name in sorted(files):
            if match(name):
                yield os.path.join(root, name)


def bad_name(path):
    stem, ext = os.path.splitext(path)
    return stem + BAD_SUFFIX + ext


def normalize_switches(argv):
    """Lower-case option names (-R -> -r, --HELP -> --help); leave values alone."""
    out = []
    for t in argv:
        if re.fullmatch(r"-[A-Za-z]", t):
            t = t.lower()
        elif t.startswith("--") and len(t) > 2:
            name, eq, val = t.partition("=")
            t = name.lower() + eq + val
        out.append(t)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="badcopy", add_help=False,
        description="Copy files from damaged media; unreadable parts become zeros. "
                    "Switches are case-insensitive (-R = -r) and have long forms.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("-r", "--recursive", dest="R", action="store_true",
                      help="recursive; destination keeps the folder tree")
    mode.add_argument("-c", "--current", dest="C", action="store_true",
                      help="current folder only (no subfolders)")
    ap.add_argument("-s", "--source", dest="S", metavar="SOURCE",
                    help="file, folder or wildcard (default: *.* in current folder)")
    ap.add_argument("-d", "--dest", "--destination", dest="D", metavar="DEST", required=True,
                    help="destination folder")
    ap.add_argument("-p", "--passes", dest="P", metavar="N", type=int, default=DEFAULT_PASSES,
                    help="retry passes over unreadable sectors (default %d; 0 = none)"
                         % DEFAULT_PASSES)
    ap.add_argument("-o", "--overwrite", dest="O", action="store_true",
                    help="overwrite existing destination files (default: skip)")
    ap.add_argument("-h", "--help", action="help", help="show this help and exit")
    args = ap.parse_args(normalize_switches(sys.argv[1:] if argv is None else argv))
    if args.P < 0:
        ap.error("-P must be 0 or more")

    base, pattern = split_source(args.S)
    dest = os.path.abspath(args.D)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        sys.exit("error: cannot create destination %s: %s" % (dest, e))

    log_path = os.path.join(dest, LOG_NAME)
    stats = {"ok": 0, "bad": 0, "skipped": 0, "failed": 0, "badbytes": 0}
    tmp = None
    prog = Progress()
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("# badcopy run %s  source=%s pattern=%s\n"
                      % (time.strftime("%Y-%m-%d %H:%M:%S"), base, pattern))
            todo = []
            skipped = 0
            for src in iter_files(base, pattern, args.R, dest):
                rel = os.path.relpath(src, base) if args.R else os.path.basename(src)
                final = os.path.join(dest, rel)
                if not args.O and (os.path.exists(final) or os.path.exists(bad_name(final))):
                    print("skip   %s (exists)" % rel)
                    skipped += 1
                    continue
                try:
                    fsize = os.path.getsize(src)
                except OSError:
                    fsize = 0
                todo.append((src, rel, final, fsize))
            stats["skipped"] = skipped
            print("Copying %d file(s), %s in total%s" % (
                len(todo), fmt_size(sum(t[3] for t in todo)),
                " (%d skipped, already exist)" % skipped if skipped else ""))
            for idx, (src, rel, final, _) in enumerate(todo, 1):
                prog.start("[%d/%d] %s" % (idx, len(todo), rel))
                try:
                    os.makedirs(os.path.dirname(final), exist_ok=True)
                    size = os.path.getsize(src)
                    tmp = final + ".badcopy.part"
                    prog.update(0, size)
                    bad = copy_file(src, tmp, size, args.P, prog.update)
                except FatalError:
                    raise
                except OSError as e:
                    prog.clear()
                    print("FAILED %s: %s" % (rel, e), file=sys.stderr)
                    log.write("FAILED\t%s\t%s\n" % (src, e))
                    stats["failed"] += 1
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
                    tmp = None
                    continue
                target = bad_name(final) if bad else final
                try:
                    if args.O:
                        for t in (final, bad_name(final)):
                            if os.path.exists(t):
                                os.remove(t)
                    os.replace(tmp, target)
                    tmp = None
                    try:
                        shutil.copystat(src, target)
                    except OSError:
                        pass
                except OSError as e:
                    raise FatalError("cannot finalise %s: %s" % (target, e))
                prog.clear()
                if bad:
                    lost = sum(n for _, n in bad)
                    stats["bad"] += 1
                    stats["badbytes"] += lost
                    print("BAD    %s -> %s  (%d bad range(s), %d bytes zeroed)"
                          % (rel, os.path.basename(target), len(bad), lost))
                    for off, n in bad:
                        log.write("BAD\t%s\toffset=%d\tlength=%d\n" % (src, off, n))
                else:
                    stats["ok"] += 1
                    print("ok     %s" % rel)
                log.flush()
    except KeyboardInterrupt:
        prog.clear()
        print("\nInterrupted.", file=sys.stderr)
        code = 130
    except FatalError as e:
        prog.clear()
        print("\nFATAL: %s" % e, file=sys.stderr)
        code = 2
    except OSError as e:
        prog.clear()
        print("\nFATAL: %s" % e, file=sys.stderr)
        code = 2
    else:
        code = 1 if (stats["bad"] or stats["failed"]) else 0
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    print("\nDone: %(ok)d ok, %(bad)d with bad areas (%(badbytes)d bytes zeroed), "
          "%(skipped)d skipped, %(failed)d failed. Log: %%s" % stats % log_path)
    return code


if __name__ == "__main__":
    sys.exit(main())
