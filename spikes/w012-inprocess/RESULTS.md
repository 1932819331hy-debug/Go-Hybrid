# Confirmatory Replication Protocol: W-012 In-Process Analyzer Spike

## 0. Preregistration Notice & Protocol Status
> [!IMPORTANT]
> **Exploratory measurements were already observed before this preregistration.**
> 
> In accordance with Go-Hybrid spike measurement protocol, this document registers a formal **confirmatory replication**. The hypotheses, targets, sample counts, and execution commands below are locked in prior to executing the 100-iteration confirmatory benchmark and test ledger.

- **Baseline Commit**: `e2a5f7d5eb90becb210df274765adfa50c99b21b` (`upstream/main`)
- **Target Branch**: `spike/w012-inprocess-analyzer`
- **Protocol Class**: Confirmatory Replication (N=100 iterations per benchmark condition)
- **Primary Metric**: Median (p50) and 90th percentile (p90) latency; mean reported as outlier-sensitive secondary.

---

## 1. Preregistered Hypotheses & Targets

### Target 1: Corpus Verdict Parity
- **Hypothesis**: The WebAssembly artifact `hy_wasm.wasm` (compiled from `compiler/crates/hy-wasm`) achieves 100.0% byte-for-byte verdict agreement with the native Rust compiler across all active corpus fixtures in `tests/checker-corpus/`.
- **Target**: 55/55 fixtures match exact expected verdicts (`accept`, `accept-managed`, `reject`).
- **Pass Criterion**: Zero mismatches.

### Target 2: Latency Advantage (Wazero vs. Subprocess)
- **Hypothesis A (Speedup)**: Steady-state in-process WASM checking will achieve an order-of-magnitude speedup over native CLI process creation on this host.
  - *Representative metric*: Ratio of p50 latencies ($T_{\text{subprocess\_p50}} / T_{\text{warm\_wasm\_p50}}$) expected $\ge 10\times$.
- **Hypothesis B (Original Latency Claim Correction)**: Issue #268's original estimate of 15–40 ms baseline subprocess latency is expected to be falsified/overestimated for fast executions on this host, with typical native subprocess p50 $< 8\text{ ms}$.

### Target 3: Wazero Cache Regimes & Break-Even
- **Hypothesis**:
  - *Case A (Cold / No Cache)*: JIT compilation overhead will be substantial (>200 ms), making one-shot uncached execution slower than native CLI.
  - *Case D (Filesystem Cache)*: `wazero.NewCompilationCacheWithDir` will reduce compilation time by $>90\%$, enabling break-even against the native CLI within $\le 5\text{ checks}$ (p50-based).
  - *Case E (Warm / Long-Lived)*: Check execution will remain sub-millisecond ($< 1.0\text{ ms}$).

### Target 4: Failure-Domain Separation
- **Hypothesis**: Infrastructural and guest engine failures will fail closed to `AnalysisResult::AnalysisFailure`, strictly disjoint from user code verdicts (`Accept` and `Reject`).
- **Expected Matrix**:
  - Valid program -> `VerdictAccept`
  - Hybrid violation -> `VerdictReject`
  - WASM trap -> `VerdictAnalysisFailure`
  - Corrupt JSON payload -> `VerdictAnalysisFailure`
  - Out-of-bounds pointer -> `VerdictAnalysisFailure`
  - Allocator failure -> `VerdictAnalysisFailure`
  - Timeout -> `VerdictAnalysisFailure`
  - Invariant: `AnalysisFailure` never returns `Accept` or `Reject`.

### Target 5: Analyzer Contract & Source Position
- **Hypothesis**:
  - `pass.Files` will contain only `.go` AST files.
  - Sibling `.hy` discovery relies on host filesystem directory scanning (`os.ReadDir`), demonstrating on-disk integration, not virtualized/unsaved editor-buffer integration.
  - Adding `.hy` to `pass.Fset` via `AddFile` and `SetLinesForContent` allows mapping exact `line:column` positions in `go vet -vettool`.

---

## 2. Planned Confirmatory Commands & Execution Plan

```bash
# 1. Corpus Parity Verification
node test_wasm_parity.js

# 2. Analyzer Contract & Exact Source Position Verification
cd spikes/w012-analyzer && go test -v -run TestAnalyzerEmpiricalPassContext .

# 3. On-disk go vet Integration Verification
go vet -vettool=./cmd/hybvet/hybvet ./testpkg

# 4. Failure-Domain & Fault Injection Verification
cd spikes/w012-analyzer && go test -v -run TestFailureDomainSeparation .

# 5. Confirmatory 100-Iteration Benchmark
go run spikes/w012-analyzer/bench/bench_wazero.go
```

---

## 3. Results Ledger (Pending Execution)
*(This section will be populated with exact raw outputs upon completion of the confirmatory run)*
