# CLAUDE.md

## Project
`badcopy.py` - single-file, stdlib-only Python (3.8+) utility that copies files from
damaged media. Unreadable regions become zeros; see README.md for user-facing behaviour.

## Conventions
- Keep it one file with no third-party dependencies. If one is ever required, the script
  must detect it missing and print install instructions (like the Python version check).
- Switches: all single-letter, case-insensitive (`normalize_switches` lower-cases option
  names only, never values), each with a long form. argparse is defined with lowercase
  options and `add_help=False` plus an explicit `-h/--help`; `args.R/C/S/D/P/O` dest names
  are intentionally uppercase.
- Fatal conditions raise `FatalError` (exit 2); per-file source errors are logged and the
  run continues. Never leave `.badcopy.part` files behind.
- Read failures are handled by bisection down to `SECTOR` (512 B), then `retry_bad` re-reads
  sectors `-P` times. Output size must always equal source size (zeros pad bad ranges).
- The source is strictly read-only (`open(src, "rb")` only). All writes - copies, `.badcopy.part`
  temp files and `badcopy.log` - go to the destination root, never next to the source.
- Files are tallied before copying (`todo` list, skips resolved up front). Progress goes through
  the `Progress` class (throttled, TTY-only); call `prog.clear()` before any other output.
- Damaged files are renamed `name_bad.ext`; skip logic checks both names.

## Testing
No test suite yet. Test by monkeypatching `badcopy.read_range` to raise `OSError(23, ...)`
for chosen offsets (flaky: succeed after N tries; dead: always fail), then check output
size, zeroed range and `badcopy.log`. Run from a scratch folder, not the repo.

## Environment
Developed on Windows 10 (PowerShell/Git Bash). Paths use `os.path`; avoid POSIX-only calls.
