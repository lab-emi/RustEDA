# Development

```sh
git clone --recurse-submodules https://github.com/lab-emi/RustEDA.git
cd RustEDA
./dev status --json
./dev doctor
./dev shared
```

The development baseline is Git, Python 3.11+, Rust 1.97.1 and Linux native
build dependencies. Component-specific requirements live under each guide.
There is no root Cargo workspace. Shared implementation lives in RustEDA Common;
applications in `apps/` depend on an exact source revision.

```sh
./dev test RustEDA-Common
./dev fmt all
./dev check RustSys RustSTA
./dev test RustEDA --no-default-features
./dev check RustEDA --no-default-features
./dev build RustROAD --release --timeout 600
```

Builds run serially, with two Cargo jobs and two test threads by default.
The default per-module limits are 8 GiB RAM, 2 GiB swap and 180 seconds.
Targets and measured resource logs live under `.work/`. Runs above 4 GiB take an
exclusive scale lock. Adjust `--memory-mib`, `--timeout`, `--jobs` and
`--test-threads` explicitly; never run an unbounded verification suite.
`doctor` checks setup and revision consistency, not EDA correctness.

Use module branches for source changes and publish their commits before updating
the root gitlinks. Keep independent module checkouts for concurrent work. Never
overwrite another worktree or a frozen benchmark producer.

## Documentation

```sh
python3 -m pip install -r docs/requirements.txt
python3 scripts/build_docs.py
```

The build stages documentation under `.work/docs-source` and writes the complete
static site to `.work/docs-site`. It does not run a server or modify module source.
The root owns navigation, shared styling and deployment. Standalone module sites
link back to the canonical RustEDA documentation.

## Repository boundaries

Run `python3 scripts/check_repository_boundaries.py` to audit tracked sources,
documentation, CI and initialized submodules for references to the retired
standalone repositories or checkout directories. The non-physical CI workflows
run this check automatically. It streams files within explicit file/byte caps.
Old verification records retain their original references and are protected by
exact SHA-256 values; local crate names and compatibility schema identifiers
remain valid. This check does not run EDA tools or routing.

Application source checks query Cargo metadata offline, including optional and
platform-specific packages. In a fresh CI cache, first run
`(cd apps && cargo fetch --locked)`. This downloads the locked
package inventory; the headless test job still builds with `--no-default-features`.

CI also bounds rustup's first-install unpacking to 256 MiB and one I/O worker,
separately from the 4 GiB address-space cap for dependency setup. See the
[rustup environment reference](https://rust-lang.github.io/rustup/environment-variables.html).
