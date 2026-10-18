# W-012 Post-Closeout Hardening — Chat Rescue Branch

Branch: `spike/w012-chat-rescue`

Base: `21e7046de71cab78d66c9c741bc900da428a393d` (same artifact tree as the pre-rescue closeout candidate, before this Chat-only hardening series).

## Status

**CORE HARDENING EXECUTION-VERIFIED.**

The core rescue implementation at commit `82b2d3b235703a92b37309bb39536c6787739d44` passed the dedicated GitHub Actions validation run `39094547848` on Ubuntu 24.04 with Go 1.26.9, Rust 1.99.0, and a freshly rebuilt `wasm32-unknown-unknown` artifact.

Validation run: <https://github.com/1932819331hy-debug/Go-Hybrid/actions/runs/39094547848>

This file deliberately does not amend or relabel the preregistered benchmark. The canonical 100-run latency numbers remain historical measurements from their recorded commit. The validation harness below produced fresh latency observations on a different GitHub-hosted Linux runner, but those numbers are **not** replacements for the preregistered benchmark and are not used to recompute the historical 18.7x figure.

The rescue branch exists because local coding-agent model quota was exhausted after several post-closeout correctness gaps were identified. The changes were authored through the GitHub connector in Chat and independently executed by GitHub Actions.

## Verified post-closeout fixes

### 1. WASM output ownership in the parity harness

`test_wasm_parity.js` copies the JSON result out of guest linear memory and calls `hy_wasm_free(outPtr, outLen)` for every check. It also rejects null/zero output buffers. The historical harness freed only the source allocation and leaked each output allocation.

This does not change the semantic parity criterion; it removes a harness-side lifetime leak that made long-loop memory observations unreliable.

### 2. Accept verdicts preserve warnings

The Go host no longer assumes `Accept => diagnostics == empty`. Hybrid explicitly permits warning diagnostics on accepted programs (notably E8xxx managed lints). `AnalysisResult.CheckInvariant` rejects only severity=`error` diagnostics on Accept.

`CheckSourceWithABI` returns accepted warning/note diagnostics instead of dropping them.

The `go/analysis` adapter intentionally reports only severity=`error` diagnostics. `analysis.Diagnostic` has no severity channel and vet treats reported diagnostics as findings; emitting an E8xxx warning through `pass.Report` would risk converting a warning that must never fail a build into a vet failure. Warning data remains available through the structured checker result.

### 3. Managed audit is an explicit opt-in ABI

The default `hy_wasm_check` ABI remains compatible with the historical `hyb check --json` envelope and therefore continues to omit the optional managed report.

A new `hy_wasm_check_managed` export mirrors `hyb check --json --report-managed`. The Go host exposes this through `CheckSourceManaged` / `Engine.CheckManaged`, and the Go `Summary` model includes the full managed audit shape:

- managed block count;
- sites resolved through managed;
- total sites;
- per-category counts;
- per-block reason/category/tickets.

This avoids silently changing the preregistered default parity contract while making the audit available to an embedding that explicitly requests it.

### 4. Unicode source columns are converted correctly

Hybrid lexer columns advance over Rust `char` values (Unicode scalar columns). Go `token.Pos` is byte-offset based. The previous adapter used `col - 1` directly as a byte offset, which is wrong after non-ASCII text.

The rescue branch converts a 1-based Hybrid rune column to the corresponding byte offset before adding it to `token.File.LineStart`.

### 5. Cache-safe supported vet entry point

Stock `go vet` cannot know that arbitrary sibling `.hy` files are inputs to an out-of-tree analyzer, so its action cache is not naturally keyed on them.

The supported command is now:

```sh
hybvet vet <packages...>
```

The wrapper:

1. captures SHA-256 for every `.hy` file under the current workspace root;
2. invokes `go vet -a -vettool=<hybvet>` so a prior Go-only action-cache hit cannot hide a Hybrid-only edit;
3. passes the encoded snapshot in `HYBRID_W012_SNAPSHOT` for a future/stricter analyzer-side dependency contract;
4. captures the workspace again after vet and fails closed if a `.hy` file was modified, added, or deleted during the run.

The executed regression covers valid → invalid → valid `.hy` edits, a second sibling `.hy`, and an unrelated Go edit.

### 6. Concurrent disposable guest instantiation

The first real `-race` validation exposed a genuine concurrency bug: parallel checks on one `WasmEngine` could fail with:

```text
module[hy_wasm.wasm] has already been instantiated
```

The cause was Wazero's default use of the module name from the WASM name section; duplicate named instances are not allowed within one Runtime. The fix instantiates each disposable guest with `wazero.NewModuleConfig().WithName("")`, the documented anonymous multi-instance mode. This preserves actual parallel execution instead of hiding the issue behind a host mutex.

After the fix, the full Go analyzer suite passed under the race detector, including the concurrent guest test.

### Snapshot limitation that remains explicit

The current rescue branch performs pre/post workspace verification in the wrapper, but the analyzer itself does **not yet consume and verify `HYBRID_W012_SNAPSHOT` for each read**. An attempted large-file connector update for that final wiring was rejected by the write safety layer.

Therefore this wrapper closes the practical stale-cache hole and detects persistent concurrent changes, but a pathological edit that occurs during analysis and is reverted to the exact original bytes before the post-run verification is not excluded by this branch. Do not describe the current implementation as a fully versioned per-read snapshot until that final wiring is added and tested.

## Executed validation ledger

Dedicated GitHub Actions run `39094547848` executed the following against freshly built artifacts:

| Gate | Result | Evidence |
|---|---|---|
| `cargo fmt --package hy-wasm -- --check` | PASS | Rust formatting gate |
| `cargo build -p hyb --release` | PASS | fresh native CLI build |
| `cargo build -p hy-wasm --target wasm32-unknown-unknown --release` | PASS | fresh WASM build |
| `go test -race ./...` | PASS | analyzer package `38.578s`; includes concurrent guest checks and hardening regressions |
| `TestDisposableGuestLifecycle20000` | PASS | 20,000 disposable guest checks in `33.86s` |
| `TestVetWrapperInvalidatesEveryHybridInput` | PASS | `2.71s` |
| `TestManagedWarningsAndAuditPreserved` | PASS | `0.33s` |
| native/WASM normalized envelope parity | PASS | `55/55` fixtures |
| corpus expected-verdict agreement | PASS | `55/55` fixtures |

The freshly built rescue WASM was `927,036` bytes (`905.3 KB`) on this runner. The parity harness observed mean `0.461 ms`, p50 `0.312 ms`, p90 `0.891 ms`, p99 `1.900 ms` per file. These are environment-specific validation observations only; they are **not** the canonical preregistered latency benchmark.

## Added regression coverage

`hardening_regression_test.go` locks:

- Accept + warning is valid;
- Accept + error violates the verdict invariant;
- rune-column → byte-offset conversion for non-ASCII source;
- default ABI preserves warning diagnostics while omitting managed audit for parity;
- managed ABI preserves warning diagnostics and managed block audit metadata.

`cache_invalidation_test.go` locks:

- Hybrid-only valid → invalid → valid changes cannot be hidden by vet caching;
- a second `.hy` sibling independently participates in freshness;
- unrelated Go edits do not break the wrapper;
- snapshot mutation/addition/deletion detection.

`engine_lifecycle_test.go` locks:

- real concurrent checks over one compiled/runtime engine remain independent under `-race`;
- 20,000 sequential disposable guest instances preserve verdict/invariant stability.

The 20k test is a lifetime/state-drift stress test, **not** a production heap/RSS leak proof.

## Reproduction

From repository root:

```sh
cd compiler
cargo fmt --package hy-wasm -- --check
cargo build -p hyb --release
cargo build -p hy-wasm --target wasm32-unknown-unknown --release
```

Then:

```sh
cd ../spikes/w012-analyzer
export HYBRID_WASM_PATH="$PWD/../../compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm"
go test -race ./...
W012_LONG_STRESS=1 go test -run '^TestDisposableGuestLifecycle20000$' -count=1 -v
go test -run '^TestVetWrapperInvalidatesEveryHybridInput$' -count=1 -v
go test -run '^TestManagedWarningsAndAuditPreserved$' -count=1 -v
```

Then from repository root:

```sh
export HYBRID_WASM_PATH="$PWD/compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm"
export HYBRID_NATIVE_PATH="$PWD/compiler/target/release/hyb"
node test_wasm_parity.js
```

## Claims that remain unproven

This successful hardening run still does not prove:

- dynamic third-party analyzer loading in stock gopls;
- unsaved `.hy` editor-buffer integration;
- a completed standalone LSP or end-to-end editor latency;
- a finished browser playground UI;
- long-duration production memory/RSS behavior;
- CI image-size / onboarding-footprint measurements requested by the original M2 wording;
- a fully versioned per-read `.hy` snapshot against an edit-and-revert race during one vet invocation.

Those are learning/architecture items, not facts established by the M1 spike.
