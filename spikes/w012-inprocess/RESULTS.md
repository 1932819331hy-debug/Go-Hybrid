# Confirmatory Replication Protocol & Results Ledger: W-012 In-Process Analyzer Spike

## 0. Preregistration Notice & Protocol Status
> [!IMPORTANT]
> **Exploratory measurements were already observed before this preregistration.**
> 
> In accordance with Go-Hybrid spike measurement protocol, this document registers a formal **confirmatory replication**. The hypotheses, targets, sample counts, and execution commands below were locked in at commit `58ad41ae78f240680525a634aad8309fc9ad7281` prior to executing the 100-iteration confirmatory benchmark and test ledger.

- **Baseline Upstream Commit**: `e2a5f7d5eb90becb210df274765adfa50c99b21b` (`upstream/main`)
- **Preregistration Commit SHA**: `58ad41ae78f240680525a634aad8309fc9ad7281`
- **Target Branch**: `spike/w012-inprocess-analyzer`
- **Protocol Class**: Confirmatory Replication (N=100 iterations per benchmark condition)
- **Primary Representative Metric**: Median (p50) and 90th percentile (p90) latency; mean reported as secondary outlier-sensitive metric.

---

## 1. Preregistered Targets & Validation Criteria

| Target # | Target Scope | Target Hypothesis | Pass Criterion | Confirmatory Result |
|---|---|---|---|---|
| **Target 1** | Corpus Parity | 55/55 fixtures match native CLI byte-for-byte | 100.0% parity (0 diffs) | **CONFIRMED (55/55, 100.0%)** |
| **Target 2** | Native CLI Subprocess | Original 15–40 ms assumption overstated; p50 < 8 ms | Documented baseline | **FALSIFIED / OVERSTATED** (p50: 5.745 ms) |
| **Target 3** | Steady-State WASM Speedup | Warm in-process check p50 < 1.0 ms; speedup $\ge 10\times$ | p50 ratio $\ge 10\times$ | **CONFIRMED** (p50: 0.272 ms, **21.1x speedup**) |
| **Target 4** | Filesystem Cache Break-Even | FS cache reduces compile by $>90\%$; break-even $\le 5$ checks | $N_{\text{break-even}} \le 5$ | **CONFIRMED** (Compile: 12.86 ms, **3 checks**) |
| **Target 5** | Failure-Domain Separation | Failures fail closed to `AnalysisFailure`, disjoint from Reject | 100% pass on 5 fault injections | **CONFIRMED** (5/5 injected faults caught) |
| **Target 6** | On-Disk Analyzer & Position | On-disk discovery works via `os.ReadDir`; exact positions via `Fset.AddFile` | Exact `line:col` in `go vet` | **CONFIRMED** (`sibling.hy:7:13`) |

---

## 2. Confirmatory Test Results (Executed at Preregistered Commit)

### 2.1 Corpus Parity Verification (`node test_wasm_parity.js`)
```text
Loaded WASM: 925098 bytes (903.4 KB)
WASM instantiation time: 2.55 ms
Verifying all 55 corpus files against WASM...
Corpus parity: 55/55 (100.0%)
WASM Check Latency per file:
  mean: 0.823 ms
  p50:  0.528 ms
  p90:  1.298 ms
  p99:  10.456 ms
```
- **Verdict**: **PASSED (100.0% parity)**.

### 2.2 Analyzer Contract & Source Position (`go test -v -run TestAnalyzerEmpiricalPassContext`)
```text
=== RUN   TestAnalyzerEmpiricalPassContext
    analyzer_test.go:62: A. pass.Files count: 1 (Only .go files parsed into Go AST)
    analyzer_test.go:66: B1. pass.OtherFiles: [] (len=0)
    analyzer_test.go:67: B2. pass.IgnoredFiles: [] (len=0)
    analyzer_test.go:73: C. Discovered .hy files via DirScan: [DirScan:bad.hy]
    analyzer_test.go:79: D. PkgPath: github.com/limitlessprojects/go-hybrid/spikes/w012-analyzer/testdata/src/samplepkg, HasTypesInfo: true
    analyzer_test.go:82: E. Reported 3 diagnostic(s) from sibling .hy analysis:
    analyzer_test.go:85:  - [hybrid] Pos=193 (.../bad.hy:6:5): [HYB-E1002] `y` is declared and never used
    analyzer_test.go:85:  - [hybrid] Pos=212 (.../bad.hy:7:5): [HYB-E1002] `z` is declared and never used
    analyzer_test.go:85:  - [hybrid] Pos=220 (.../bad.hy:7:13): [HYB-E3003] `x` is read after ownership was moved away
--- PASS: TestAnalyzerEmpiricalPassContext (0.44s)
PASS
```

### 2.3 On-Disk `go vet -vettool` Execution
```text
$ go vet -vettool=./cmd/hybvet/hybvet ./testpkg
testpkg/sibling.hy:6:5: [HYB-E1002] `y` is declared and never used — Go refuses to build the emitted `y := ...` (declared and not used) (HYB-E1002)
testpkg/sibling.hy:7:5: [HYB-E1002] `z` is declared and never used — Go refuses to build the emitted `z := ...` (declared and not used) (HYB-E1002)
testpkg/sibling.hy:7:13: [HYB-E3003] `x` is read after ownership was moved away
exit status 1
```
- **Verdict**: **Exact on-disk .hy positions: CONFIRMED**.

### 2.4 Failure-Domain Separation & Fault Injection (`go test -v -run TestFailureDomainSeparation`)
```text
=== RUN   TestFailureDomainSeparation
    fault_injection_test.go:87: [PASS] Valid program -> Accept
    fault_injection_test.go:104: [PASS] Rule violation -> Reject (1 diagnostics)
=== RUN   TestFailureDomainSeparation/Fault_WasmTrap
    fault_injection_test.go:122: [PASS] Fault Injection 1 (WASM Trap) -> AnalysisFailure: infrastructure error: WASM trap/panic during execution: wasm trap: unreachable instruction executed
=== RUN   TestFailureDomainSeparation/Fault_MalformedJSON
    fault_injection_test.go:143: [PASS] Fault Injection 2 (Malformed JSON) -> AnalysisFailure: infrastructure error: malformed diagnostic envelope: invalid character 'T' looking for beginning of value
=== RUN   TestFailureDomainSeparation/Fault_InvalidPointerBounds
    fault_injection_test.go:163: [PASS] Fault Injection 3 (Invalid Memory Bounds) -> AnalysisFailure: infrastructure error: out-of-bounds guest memory read: ptr=50000, len=100
=== RUN   TestFailureDomainSeparation/Fault_AllocFailure
    fault_injection_test.go:182: [PASS] Fault Injection 4 (Alloc Failure) -> AnalysisFailure: infrastructure error: guest memory alloc failed: out of memory in guest wasm allocator
=== RUN   TestFailureDomainSeparation/Fault_Timeout
    fault_injection_test.go:198: [PASS] Fault Injection 5 (Execution Timeout) -> AnalysisFailure: infrastructure error: context timeout before instantiation: context deadline exceeded
--- PASS: TestFailureDomainSeparation (0.37s)
PASS
```

### 2.5 Confirmatory 100-Iteration Benchmark Raw Output
```text
================================================================================
W-012 HARDENING BENCHMARK: Wazero vs Native Subprocess (100 iterations/case)
================================================================================
[Baseline] Native CLI Subprocess:   min=4.566ms, p50=5.745ms, p90=6.491ms, p99=6.846ms, mean=5.698ms
[CASE A] Fresh Runtime (No Cache):  total_mean=362.895ms (compile=361.790ms, inst=0.221ms, check=0.775ms)
         Compile Stats: min=340.781ms, p50=350.955ms, p90=375.974ms, p99=537.408ms, mean=361.790ms
[CASE B] Reused CompiledModule:     total_mean=1.005ms (inst=0.182ms, check=0.813ms)
         Total Stats:   min=0.570ms, p50=0.908ms, p90=1.509ms, p99=2.140ms, mean=1.005ms
[CASE C] New Runtime (In-Memory Cache): total_mean=14.461ms (compile=13.368ms, inst=0.259ms, check=0.819ms)
         Compile Stats: min=9.608ms, p50=9.924ms, p90=10.383ms, p99=10.638ms, mean=13.368ms
[CASE D] New Runtime (Filesystem Cache): total_mean=14.368ms (compile=12.975ms, inst=0.167ms, check=0.912ms)
         Compile Stats: min=12.389ms, p50=12.860ms, p90=13.570ms, p99=14.453ms, mean=12.975ms
================================================================================
BREAK-EVEN QUANTITATIVE ANALYSIS (100 ITERATIONS CONFIRMATORY):
 [Primary: p50-based Metrics]
  • Native CLI Subprocess p50:                 5.745 ms
  • Pure Warm WASM In-Process p50:             0.272 ms
  • Representative Steady-State Speedup (p50): 21.1x
  • Latency Advantage per Check (delta_T p50): 5.473 ms
  • Cold Compile Overhead (T_compile_cold p50):350.955 ms
  -> Break-Even (Cold / No Cache, p50):        N_break_even ≈ 64.1 checks (ceil: 65 checks)
  • FS Cached Compile (T_compile_fs p50):      12.860 ms
  -> Break-Even (Filesystem Cache, p50):       N_break_even ≈ 2.3 checks (ceil: 3 checks)

 [Secondary: Outlier-Sensitive Mean Metrics]
  • Native CLI Subprocess Mean:                5.698 ms
  • Pure Warm WASM In-Process Mean:            0.286 ms
  • Ratio of Means (Mean Speedup):             19.9x
  -> Break-Even (Cold / No Cache, Mean):       N_break_even ≈ 66.9 checks (ceil: 67 checks)
  -> Break-Even (Filesystem Cache, Mean):      N_break_even ≈ 2.4 checks (ceil: 3 checks)
================================================================================
```

---

## 3. Protocol Verification Sign-Off
All 6 preregistered hypotheses have completed confirmatory execution:
1. Corpus parity: **100.0% confirmed**.
2. Baseline subprocess latency: **5.745 ms p50** (Falsifying the 15–40 ms assumption).
3. Representative warm speedup: **21.1x by p50** (5.745 ms vs 0.272 ms).
4. Break-even threshold: **65 checks (cold)** vs **3 checks (filesystem-cached)**.
5. Failure-domain isolation: **100% verified across 5 injection faults**.
6. On-disk Analyzer execution: **Exact source line/col positions confirmed**.
