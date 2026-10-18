# W-012: 25-file, 100-edit request-path comparison

Purpose: independently measure the corrected public Go/Wazero request path against the native hyb CLI on the *same machine*, using real file modifications and the existing pre-registered corpus oracle.

**This is not a gopls or LSP benchmark.** It measures an on-disk single-file edit-to-check request proxy. It cannot establish an end-to-end keystroke-to-diagnostic time or successful integration of unsaved Hybrid buffers into stock gopls.

## Method

1. Create a temporary Go module with one ordinary Go anchor and 25 .hy source files copied from one pre-registered checker-corpus fixture.
2. Warm both check paths equally. Modify a different .hy file for every edit (round-robin through all 25), with 100 sequential, constant-width, non-cumulative edits. Start the request timer only after writing modified bytes.
3. Evaluate the changed file with (A) native hyb check PATH --json (new subprocess and JSON decoder each request), and (B) public Engine.Check(source) (in-process Wazero with already-compiled module and disposable guest per request). Alternate request order on odd and even edits.
4. Fail if the native exit code is inconsistent with a valid parsed envelope; if the registered expected verdict fails; or if modeled diagnostic codes, counts, severities, Tier-0 rules or source spans differ. Timing never overrides correctness.
5. Record all 200 durations plus p50/p90/p95/p99/mean, separate cold WASM compilation/init, SHA-256 hashes and sizes of the native and WASM artifacts, and basic host metadata.
6. CI saves the raw JSON as an upload-artifact attachment for the exact workflow run, with 7-day retention. Performance figures must remain associated with their exact tested commit and host.

## Reproduction

From the root of the repository, first build both artifacts with the toolchain versions pinned in tools/env.sh:

    (cd compiler && cargo build --locked --release -p hyb)
    (cd compiler && cargo build --locked --release -p hy-wasm --target wasm32-unknown-unknown)
    (cd spikes/w012-analyzer && go run ./cmd/editbench -root ../.. -files 25 -edits 100 -warmup 10 -output /tmp/w012-edit-benchmark.json)

## Interpretation and falsification

The benchmark models only the request made *after* an edit. The native path must create a subprocess and read the file; Engine.Check receives source bytes in-process as an embedding host could. The paths are not identical user interfaces and no complete workspace reanalysis is performed. Cold compile time is isolated, never hidden in or misrepresented as warm request latency.

No assertion is made about editor startup, stock gopls, LSP, live type bridging, Docker image size, binary release packaging, or production RSS/long-duration memory use. The original M2 goal requires additional, independently verifiable experiments for these costs. The public Engine.Check may remain slower than the native subprocess; if so, report that falsification instead of substituting a different (faster) reused-instance prototype.
