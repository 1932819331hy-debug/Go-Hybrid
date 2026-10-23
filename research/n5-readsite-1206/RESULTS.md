# N5 read-site spike results

All five reported #1206 Part 2 shapes **still accept and build, then panic on
first write** on main `7d63d5fc25ceb525c2d0f6ec89a4ad608a6a5011`.
This reproduces planners1490's report; it is not a new discovery or a fix.
Closed-channel comma-ok has the same failure. Seven valid controls preserve
declaration allocation, existing-map identity, absent-key membership, and
closed-receive zero/false behavior, and exactly-once key evaluation. The
evaluation-count control needed a
syntax-only fixture correction, recorded below.

## Exact tested source and toolchain

- Published preregistration: `9efa5aba896ae92f0f07fa7946ac986d68aa980e`.
- Compiler source tree: `3afc2ae23581817a37f1398014760431091fce2d`, identical
  to the compiler tree on the main base; the harness asserts equality and
  rejects dirty compiler files before each run.
- Go: `go1.26.9 linux/amd64`; Rust: `1.99.0 (b940084d7 2026-09-28)`.
- Actual binary: this worktree's `compiler/target/debug/hyb`, SHA256
  `7b804fdaee3938f653f14a56bcefca5d652b4306e1bacd7288c2ff811d7c4b4c`.
- Cargo built `hyb` from that worktree with `-j2`, debug info disabled, under
  `flock /tmp/heavy.lock`; no different PR's binary, toolchain drift bypass,
  or production source changes. Build finished in 9.55s with existing warnings.
- The memory guard observed a 16 GiB cgroup limit and approximately 6.36 GiB
  in use before the job. Raw environment/build output: `results/heavy.log`.

## Observed matrix

| Case | Check / build / run exit | Observed result |
|---|---|---|
| B1 direct missing nested map | 0 / 0 / 2 | nil-map write panic |
| B2 extracted missing map | 0 / 0 / 2 | nil-map write panic |
| B3 extracted missing struct | 0 / 0 / 2 | nil-map field write panic |
| B4 missing struct comma-ok | 0 / 0 / 2 | nil-map field write panic in `!ok` branch |
| B5 exhausted closed receive | 0 / 0 / 2 | nil-map write panic |
| X1 exhausted closed receive comma-ok | 0 / 0 / 2 | nil-map write panic in `!ok` branch |
| C1 declaration / struct / new | 0 / 0 / 0 | `1 2 3` |
| C2 existing inner-map aliases | 0 / 0 / 0 | `2 2 true 1` |
| C3 missing read only | 0 / 0 / 0 | `false 0 0` |
| C4 existing struct-map alias | 0 / 0 / 0 | `4 true 1` |
| C5 closed scalar comma-ok | 0 / 0 / 0 | `0 false` |
| C6 repeated closed map reads | 0 / 0 / 0 | `false false 0 0` |
| C7 original evaluation fixture | 1 / 1 / not run | HYB-E0002: incorrect `-> string` syntax |
| C7b return-type correction | 1 / 1 / not run | HYB-E0002: explicit semicolons rejected |
| C7c multiline evaluation control | 0 / 0 / 0 | `1 1 5` |

Initial harness exit is **1**, not a fully matching matrix: the C7 prediction
failed because of a research fixture defect, not an N5 compiler finding.
The original source and logs remain committed. C7b removed only the two `->`
return separators; a second syntax error (explicit semicolons) still prevented
execution. Its logs are in `results-c7b/`. C7c uses multiline helper bodies,
passes a lightweight checker preflight, and keeps predicted output `1 1 5`.
C7c then passed check/build/run with byte-exact `1 1 5` at published research
head `89f510e4700dd681285e9c3541b617eac180465d`, using the same binary SHA256.
Its focused harness exited 0. Both prior fixture failures remain evidence
rather than being overwritten.

## Interpretation and next action

The emitted Go directly reads `mm[key]` or `<-ch` and then writes the resulting
nil map. B4 emits the original comma-ok read and false branch unchanged. This
matches the audited declaration-only initializer paths; compiler acceptance
is not evidence that the runtime guarantee holds.

The recommended bounded next implementation is read-result normalization for
B2–B5 plus a separately admitted nested-slot write for B1. Preserve ordinary
read copies, original comma-ok, present-map aliases, independent closed-receive
zeros, and single evaluation/order. A write-only pointer guard does not preserve
aliases copied before allocation. Direct nested insertion changes membership
and needs an explicit maintainer decision. `DESIGN.md` compares eager, lazy,
and fail-closed alternatives and specifies the minimal scope and future gates.
No N3/N5/D11 text is changed and no compiler fix is claimed here.

Fresh source snapshots show #1207 (`41d6bb7`) and #1208 (`429665c`) add
allocation-site helpers/array initialization, not read-site normalization.
The reporter explicitly leaves B1–B5 open in comment 6350167543 and #1208.
Saved bodies include historical competing-contribution assertions; these are
attributed statements, not independently verified credit or CI verdicts. The
fresh #1207 patch already changes CI/Makefile/README census surfaces, so the
older comment's census allegation must not be repeated as current fact.

## Evidence index and resumption

- `PREREGISTRATION.md`: frozen predictions plus the documented C7 amendment.
- `fixtures/`, `cases.json`, `run.py`: minimal reproducible sources/harness.
- `results/provenance.json`, `results/matrix.json`: exact initial provenance and
  complete outcomes, including the failed prediction.
- `results/<case>/{check,build,run}.json`: raw argv, exit code, stdout, stderr,
  and wall time; each built case also includes `prog.go`.
- `results/heavy.log`: unabridged environment, compiler build, and initial run.
- `results-c7b/`, `results-c7c/`: preserved failed syntax correction and final
  successful evaluation control, including their provenance and raw logs.
- `RAW-SHA256.txt`: checksum index of raw result/source/fixture/harness files.
- `sources/`: fresh issue/body/comments and competing PR metadata/patches.
- `DESIGN.md`: source explanation, design comparison, bounded recommendation,
  and four explicit semantic questions for maintainers.

For a fresh run from this worktree root, source
`/workspace/go-hybrid-repair-work/toolchain-env.sh`, verify the pinned versions
and memory, publish any new preregistered inputs, then acquire the heavy lock.
Build with `cargo build --manifest-path compiler/Cargo.toml -p hyb -j2` and run
`python3 research/n5-readsite-1206/run.py --output results-new`. The original C7 and C7b
are intentionally retained and still predict incorrectly, so that full historical
matrix exits 1. Use `--case C7c_evaluation_once --output results-c7c-new` for the
corrected control. Never overwrite committed raw evidence with a rerun.

Limits: one Linux/amd64 main source snapshot; no full-suite or performance
claim, no alternative-design implementation measurements, no buffered-channel
payload test, no interprocedural provenance proof. Those are future compiler
patch obligations, not gaps concealed by this bounded reproduction.
