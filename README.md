# badcopy

Copy files that live (partly) on bad blocks. Readable parts are copied, unreadable
parts are filled with zeros so every offset stays correct, and the damage is logged.

Pure Python standard library, Python 3.8+. Nothing to install. If Python is missing:
`winget install Python.Python.3.12` (Windows) or https://www.python.org/downloads/.

## Usage

```
python badcopy.py (-R | -C) [-S source] -D destination [-P n] [-O]
```

| Switch | Long form | Meaning |
|---|---|---|
| `-R` | `--recursive` | Recurse into subfolders; folder tree is preserved in the destination |
| `-C` | `--current` | Current/source folder only (one of `-R`/`-C` is required) |
| `-S` | `--source` | File, folder or wildcard in the file-name part, e.g. `D:\pics\*.jpg`. Default: everything in the current folder |
| `-D` | `--dest`, `--destination` | Destination folder (created if needed). Required |
| `-P n` | `--passes` | Retry passes over unreadable sectors. Default 3, `0` = none |
| `-O` | `--overwrite` | Overwrite existing destination files (default: skip them) |
| `-H` | `--help` | Show help |

Switches are case-insensitive (`-r` = `-R`, `--HELP` = `--help`). Give values as a
separate argument (`-D E:\out`, not `-DE:\out`).

Examples:

```
python badcopy.py -R -S D:\photos -D E:\rescue
python badcopy.py -C -S "D:\photos\*.jpg" -D E:\rescue -P 5
python badcopy.py -c -d E:\rescue
```

A man page is in `badcopy.1` (`man -l badcopy.1`).

## How it works

1. Reads 1 MB blocks. A failed block is split in half repeatedly, down to 512-byte
   sectors, so only the truly unreadable sectors are lost.
2. After the file is read, the remaining bad sectors are retried `-P` times; recovered
   data is patched in. Retries help with flaky sectors; dead sectors stay dead, so
   more than ~3-5 passes rarely pays off, and each pass is slow on failing drives.
3. Still-unreadable sectors are zero-filled.
4. A file with unreadable parts is saved as `name_bad.ext`; clean files keep their name.
5. `badcopy.log` in the destination root folder (never on the source drive) lists every bad range (`offset`, `length`) and any
   file that could not be opened at all.

The source is only ever read; nothing is written to it.
Files are written as `*.badcopy.part` and renamed when complete, so an interrupted run
leaves no half-written files. The destination folder is excluded when it lies inside
the source tree.

## Output

After the files are tallied it prints `Copying N file(s), X MB in total` (files that
already exist at the destination are listed as skipped first). While each file is
copied, a one-line indicator shows `[i/N] name  45.0%  12.3 MB of 27.2 MB`, and
`retry 2/3` during retry passes (sizes shown in bytes, KB, MB or GB as fitting). The
indicator is only shown on a terminal, not when output is redirected to a file.

## Exit codes

`0` all clean · `1` some files had bad areas or failed · `2` fatal error (e.g. destination
full) · `130` interrupted.

## Tips

For a heavily failing disk, copy the most important files first and consider a
sector-level imaging tool (e.g. GNU ddrescue) before using file-level copying.
