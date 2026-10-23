# N5 read-site spike: preregistration

2026-10-23. Base main: `7d63d5fc25ceb525c2d0f6ec89a4ad608a6a5011`.
Research only: no compiler, language-spec, or checker-corpus changes. This
reproduces planners1490's #1206 Part 2; no discovery/reward claim is made.
Issue/comment/competing-PR snapshots are in `sources/`. The reporter explicitly
keeps B1–B5 open in comment 6350167543 and #1208. #1207's closing keyword does
not establish read-site coverage. No competing PR compiler will be used.

## Hypotheses frozen before execution

All 13 fixtures must pass `hyb check` and `hyb build` on this main source.
B1–B5 and X1 (closed-channel comma-ok) should then exit nonzero with
`assignment to entry in nil map`; C1–C7 should run with byte-exact stdout in
`cases.json`. A check/build failure is a distinct finding, never a successful
reproduction of the runtime bug. Unexpected findings remain in raw logs.

| Cases | Discriminating purpose |
|---|---|
| B1 | Direct nested missing-map write |
| B2 | Extracted missing-map local; outer membership versus local storage |
| B3 | Missing map element with struct/map field |
| B4 | Struct comma-ok must retain false despite normalization |
| B5 / X1 | Closed receive one-value / comma-ok map zeros |
| C1 | Existing declaration/new(T) lowering remains functional |
| C2 | Present inner-map reads alias; original membership remains true |
| C3 | Read-only missing lookup does not insert; ok false, lengths zero |
| C4 | Present struct copy preserves inner-map alias identity |
| C5 | Closed scalar receive remains zero/false |
| C6 | Repeated closed-map receives stay zero/false without writes |
| C7 | Side-effecting outer/inner keys evaluated once for existing entry |

## Execution and evidence

Before any heavy command: commit this protocol, fixtures, and harness; lead
publishes it and confirms publication. Source the shared toolchain helper from
this worktree root; Go 1.26.9, Rust 1.99.0, unique `compiler/target`, jobs=2,
no drift bypass. Check RAM (at least 1 GiB available), then serialize the
compiler build and fixture run with `flock /tmp/heavy.lock`. Build actual main
compiler once with `cargo build --manifest-path compiler/Cargo.toml -p hyb -j2`.
Bound each check/build/run by 30/120/10 seconds and the overall job by 1800s.

`run.py` records exact research commit, source tree, tool versions, binary
SHA256, each argv/exit/stdout/stderr, and emitted Go. Generated binaries live
in a temporary directory, not the repository. The matrix is characterization,
not a fix suite; an expected panic is labelled reproduced, not language-safe.
No workspace/full corpus sweep or benchmarks are needed for this bounded spike.

## Design deliverable and stop rule

Compare eager recursive normalization, lazy first-write allocation with
explicit writeback, and fail-closed admission. Address missing vs present reads,
aliases before first write, membership/allocation visibility, evaluation order
and exactly-once evaluation, comma-ok, independent closed receive zero values,
and Go-owned N6 boundaries. Recommend a bounded implementation only after
recording the identity/writeback decision needed from maintainers. D11 is open;
this spike cannot resolve it or silently amend N3/N5. Stop after the matrix,
source audit, and decision report, unless a harness issue blocks evidence.

## Recorded amendment after initial run

The initial matrix finished with exit 1: B1–B5/X1 reproduced and C1–C6 matched,
but C7 failed parsing because the fixture used `-> string` instead of Hybrid's
Go-style `string` result type. Original C7 source/logs and the initial matrix
are retained unchanged. C7b changes only those two return-type separators;
its original predicted output `1 1 5` is unchanged. The harness now accepts
`--case` and a separate `--output` directory to preserve initial evidence.
Publish this amendment before the one-case C7b build/run; no compiler rebuild
is necessary because compiler source and the binary are unchanged.
