# Command line

```text
rusteda-studio -w <DIR> [--rev <REV>] [--select <HANDLE>...] [--print] [--max-rows <N>]
               [--poll-ms <MS>] [--debounce-ms <MS>] [--fanin <N>] [--fanout <N>]
               [--split flattened|core] [-v]
```

The workbench is a client process of one workspace. It is not a daemon: it runs while its
window is open, or, with `--print`, until it has printed (Rule 2.1).

**STUDIO-REQ-0220** Options: `-w/--workspace DIR` (required) names the workspace; `--rev` names
the revision to display, `rev<N>` (zero padding accepted) pinned, or `HEAD` (the default)
followed live; `--select` takes `path<N>` and `inst<N>` handles of the displayed revision;
`--print` prints the panels instead of opening the window, with at most `--max-rows` rows per
list (default 20); `--poll-ms` (default 250, at least 10) and `--debounce-ms` (default 150) set
the watcher; `--fanin` and `--fanout` set the cone levels (at most 16); `--split` sets the
hierarchy mode (STUDIO-REQ-0030); `-v/--verbose` logs watcher events, loads and shared
selections on standard error.

**STUDIO-REQ-0221** Errors are printed on standard error as diagnostic lines
(STUDIO-REQ-0201). The exit status is 0 on success, 2 for a bad command line (`U0091`: an
unknown option, a missing workspace, a `--rev` that is not a revision, a `--select` handle that
is not `path<N>` or `inst<N>`, a `--split` mode that does not exist), and 1 when the workspace or
the revision cannot be read (`U0001` to `U0003`), when a `--select` handle names nothing in the
displayed revision (`U0043`), or when the window cannot open (`U0090`). `--help` and `--version`
print and exit 0.

**STUDIO-REQ-0222** `--print` prints the panels as text from the same model as the window: the
revision with its parent, command and timing status; the hierarchy with its counts per level and
its first scopes; the timing scope and the corner summaries; the path table; the focused path
with its points; the cone with its counts and reached budgets; the cross-probe's lines; the
shared selection; and every diagnostic. The text contains no times and no load durations, so the
same workspace prints the same text.

**STUDIO-REQ-0223** `--select` replaces the selection with the named objects, in their order,
and shares it once in `ui_state.json` (STUDIO-REQ-0142), as a user's selection would; with
`--print` the shared selection is printed.

**STUDIO-REQ-0224** The workbench never reads standard input; it runs with a closed or null
standard input, and its window closes the process.

**STUDIO-REQ-0225** A build without the `gui` feature has no window: `--print` works, and a start
without it is the error `U0090` with exit status 1.
