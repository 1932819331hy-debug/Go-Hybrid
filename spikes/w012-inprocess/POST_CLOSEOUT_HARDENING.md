# W-012 Post-Closeout Hardening — Chat Rescue Branch

Branch: `spike/w012-chat-rescue`

Base: `21e7046de71cab78d66c9c741bc900da428a393d` (same artifact tree as the pre-rescue closeout candidate, before this Chat-only hardening series).

## Status

**STATIC HARDENING CANDIDATE — NOT YET EXECUTION-VERIFIED.**

This file deliberately does not amend or relabel the preregistered benchmark. The canonical 100-run latency numbers remain historical measurements from their recorded commit. No new performance claim is made for this rescue branch until it is rebuilt and executed.

The rescue branch exists because the local coding agents exhausted their model quota after identifying several post-closeout correctness gaps. The changes below were authored through the GitHub connector in Chat and have not yet been compiled or tested in a local/CI runner.

## Static fixes in this branch

### 1. WASM output ownership in the parity harness

`test_wasm_parity.js` now copies the JSON result out of guest linear memory and calls `hy_wasm_free(outPtr, outLen)` for every check. It also rejects null/zero output buffers. The historical harness freed only the source allocation and leaked each output allocation.

This does not change the semantic parity criterion; it removes a harness-side lifetime leak that made long-loop memory observations unreliable.

### 2. Accept verdicts preserve warnings

The Go host no longer assumes `Accept => diagnostics == empty`. Hybrid explicitly permits warning diagnostics on accepted programs (notably E8xxx managed lints). `AnalysisResult.CheckInvariant` now rejects only severity=`error` diagnostics on Accept.

`CheckSourceWithABI` returns accepted warning/note diagnostics instead of dropping them.

The `go/analysis` adapter intentionally reports only severity=`error` diagnostics. `analysis.Diagnostic` has no severity channel and vet treats reported diagnostics as findings; emitting an E8xxx warning through `pass.Report` would risk converting a warning that must never fail a build into a vet failure. Warning data remains available through the structured checker result.

### 3. Managed audit is an explicit opt-in ABI

The default `hy_wasm_check` ABI remains compatible with the historical `hyb check --json` envelope and therefore continues to omit the optional managed report.

A new `hy_wasm_check_managed` export mirrors `hyb check --json --report-managed`. The Go host exposes this through `CheckSourceManaged` / `Engine.CheckManaged`, and the Go `Summary` model now includes the full managed audit shape:

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

Regression coverage exercises valid → invalid → valid `.hy` edits, a second sibling `.hy`, and an unrelated Go edit.

### Snapshot limitation that remains explicit

The current rescue branch performs pre/post workspace verification in the wrapper, but the analyzer itself does **not yet consume and verify `HYBRID_W012_SNAPSHOT` for each read**. An attempted large-file connector update for that final wiring was rejected by the write safety layer.

Therefore this wrapper closes the practical stale-cache hole and detects persistent concurrent changes, but a pathological edit that occurs during analysis and is reverted to the exact original bytes before the post-run verification is not excluded by this branch. Do not describe the current implementation as a fully versioned per-read snapshot until that final wiring is added and tested.

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

## Mandatory validation before this branch can be called verified

From repository root:

```sh
cd compiler
cargo fmt --all -- --check
cargo build -p hyb --release
cargo build -p hy-wasm --target wasm32-unknown-unknown --release
```

Then:

```sh
cd ../spikes/w012-analyzer
export HYBRID_WASM_PATH="$PWD/../../compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm"
go test -race ./...
go test -run '^TestVetWrapperInvalidatesEveryHybridInput$' -count=1 -v
go test -run '^TestManagedWarningsAndAuditPreserved$' -count=1 -v
```

Then from repository root:

```sh
export HYBRID_WASM_PATH="$PWD/compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm"
export HYBRID_NATIVE_PATH="$PWD/compiler/target/release/hyb"
node test_wasm_parity.js
```

Required outcomes before promotion:

- Rust formatting/build succeeds from this branch;
- Go analyzer tests pass under `-race`;
- cache invalidation regression passes;
- managed/warning regression passes against the freshly rebuilt WASM export;
- normalized native/WASM default-envelope parity remains 100%;
- no historical 18.7x benchmark number is relabeled as a measurement of this changed wrapper.

## Claims that remain unproven

Even if all validation above passes, this branch still does not prove:

- dynamic third-party analyzer loading in stock gopls;
- unsaved `.hy` editor-buffer integration;
- a completed standalone LSP or end-to-end editor latency;
- a finished browser playground UI;
- long-duration production memory behavior;
- CI image-size / onboarding-footprint measurements requested by the original M2 wording.

Those are learning/architecture items, not facts established by the M1 spike.
