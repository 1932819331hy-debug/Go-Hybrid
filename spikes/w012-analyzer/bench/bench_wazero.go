package main

import (
	"context"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"time"

	"github.com/tetratelabs/wazero"
	analyzer "github.com/limitlessprojects/go-hybrid/spikes/w012-analyzer"
)

type LatencyStats struct {
	Min  float64
	P50  float64
	P90  float64
	P99  float64
	Mean float64
}

func calcStats(samples []float64) LatencyStats {
	if len(samples) == 0 {
		return LatencyStats{}
	}
	sorted := make([]float64, len(samples))
	copy(sorted, samples)
	sort.Float64s(sorted)

	var sum float64
	for _, v := range sorted {
		sum += v
	}

	p := func(percent float64) float64 {
		idx := int(math.Ceil(percent*float64(len(sorted)))) - 1
		if idx < 0 {
			idx = 0
		}
		if idx >= len(sorted) {
			idx = len(sorted) - 1
		}
		return sorted[idx]
	}

	return LatencyStats{
		Min:  sorted[0],
		P50:  p(0.50),
		P90:  p(0.90),
		P99:  p(0.99),
		Mean: sum / float64(len(sorted)),
	}
}

func main() {
	ctx := context.Background()
	wasmPath := "/Users/pro/Go-Hybrid/compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm"
	wasmBytes, err := os.ReadFile(wasmPath)
	if err != nil {
		panic(err)
	}

	hybPath := "/Users/pro/Go-Hybrid/compiler/target/release/hyb"
	testHyPath := "/Users/pro/Go-Hybrid/tests/checker-corpus/08-a3-map-slot-invalidated.hy"
	testHyBytes, err := os.ReadFile(testHyPath)
	if err != nil {
		panic(err)
	}
	testHyStr := string(testHyBytes)

	iterations := 100

	fmt.Println("================================================================================")
	fmt.Printf("W-012 HARDENING BENCHMARK: Wazero vs Native Subprocess (%d iterations/case)\n", iterations)
	fmt.Println("================================================================================")

	// 0. Native Subprocess Benchmark
	var subprocSamples []float64
	for i := 0; i < iterations; i++ {
		t0 := time.Now()
		cmd := exec.Command(hybPath, "check", testHyPath)
		_ = cmd.Run() // exit code 1 on reject expected
		subprocSamples = append(subprocSamples, float64(time.Since(t0).Microseconds())/1000.0)
	}
	subprocStats := calcStats(subprocSamples)
	fmt.Printf("[Baseline] Native CLI Subprocess:   min=%.3fms, p50=%.3fms, p90=%.3fms, p99=%.3fms, mean=%.3fms\n",
		subprocStats.Min, subprocStats.P50, subprocStats.P90, subprocStats.P99, subprocStats.Mean)

	// CASE A: Fresh process / fresh runtime / no compilation cache
	var caseACompile, caseAInstantiate, caseACheck, caseATotal []float64
	for i := 0; i < iterations; i++ {
		t0 := time.Now()
		r := wazero.NewRuntime(ctx)

		tCompileStart := time.Now()
		compiled, err := r.CompileModule(ctx, wasmBytes)
		if err != nil {
			panic(err)
		}
		tCompile := float64(time.Since(tCompileStart).Microseconds()) / 1000.0

		tInstStart := time.Now()
		mod, err := r.InstantiateModule(ctx, compiled, wazero.NewModuleConfig())
		if err != nil {
			panic(err)
		}
		tInst := float64(time.Since(tInstStart).Microseconds()) / 1000.0

		abi, err := analyzer.NewWazeroInstanceABI(mod)
		if err != nil {
			panic(err)
		}

		tCheckStart := time.Now()
		res := analyzer.CheckSourceWithABI(ctx, abi, testHyStr)
		if res.Verdict != analyzer.VerdictReject {
			panic("expected reject")
		}
		tCheck := float64(time.Since(tCheckStart).Microseconds()) / 1000.0

		_ = mod.Close(ctx)
		_ = r.Close(ctx)

		tTotal := float64(time.Since(t0).Microseconds()) / 1000.0

		caseACompile = append(caseACompile, tCompile)
		caseAInstantiate = append(caseAInstantiate, tInst)
		caseACheck = append(caseACheck, tCheck)
		caseATotal = append(caseATotal, tTotal)
	}
	sACompile := calcStats(caseACompile)
	sAInst := calcStats(caseAInstantiate)
	sACheck := calcStats(caseACheck)
	sATotal := calcStats(caseATotal)

	fmt.Printf("[CASE A] Fresh Runtime (No Cache):  total_mean=%.3fms (compile=%.3fms, inst=%.3fms, check=%.3fms)\n",
		sATotal.Mean, sACompile.Mean, sAInst.Mean, sACheck.Mean)
	fmt.Printf("         Compile Stats: min=%.3fms, p50=%.3fms, p90=%.3fms, p99=%.3fms, mean=%.3fms\n",
		sACompile.Min, sACompile.P50, sACompile.P90, sACompile.P99, sACompile.Mean)

	// CASE B: Same process / same wazero.Runtime / compiled module reused
	var caseBInst, caseBCheck, caseBTotal []float64
	rB := wazero.NewRuntime(ctx)
	compiledB, err := rB.CompileModule(ctx, wasmBytes)
	if err != nil {
		panic(err)
	}

	for i := 0; i < iterations; i++ {
		t0 := time.Now()
		mod, err := rB.InstantiateModule(ctx, compiledB, wazero.NewModuleConfig())
		if err != nil {
			panic(err)
		}
		tInst := float64(time.Since(t0).Microseconds()) / 1000.0

		abi, err := analyzer.NewWazeroInstanceABI(mod)
		if err != nil {
			panic(err)
		}

		tCheckStart := time.Now()
		res := analyzer.CheckSourceWithABI(ctx, abi, testHyStr)
		if res.Verdict != analyzer.VerdictReject {
			panic("expected reject")
		}
		tCheck := float64(time.Since(tCheckStart).Microseconds()) / 1000.0

		_ = mod.Close(ctx)
		tTotal := float64(time.Since(t0).Microseconds()) / 1000.0

		caseBInst = append(caseBInst, tInst)
		caseBCheck = append(caseBCheck, tCheck)
		caseBTotal = append(caseBTotal, tTotal)
	}
	_ = rB.Close(ctx)
	sBInst := calcStats(caseBInst)
	sBCheck := calcStats(caseBCheck)
	sBTotal := calcStats(caseBTotal)

	fmt.Printf("[CASE B] Reused CompiledModule:     total_mean=%.3fms (inst=%.3fms, check=%.3fms)\n",
		sBTotal.Mean, sBInst.Mean, sBCheck.Mean)
	fmt.Printf("         Total Stats:   min=%.3fms, p50=%.3fms, p90=%.3fms, p99=%.3fms, mean=%.3fms\n",
		sBTotal.Min, sBTotal.P50, sBTotal.P90, sBTotal.P99, sBTotal.Mean)

	// CASE C: New runtime / shared in-memory compilation cache
	memCache := wazero.NewCompilationCache()
	// Warm cache once
	rWarm := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithCompilationCache(memCache))
	cWarm, _ := rWarm.CompileModule(ctx, wasmBytes)
	_ = cWarm.Close(ctx)
	_ = rWarm.Close(ctx)

	var caseCCompile, caseCInst, caseCCheck, caseCTotal []float64
	for i := 0; i < iterations; i++ {
		t0 := time.Now()
		rC := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithCompilationCache(memCache))

		tCompStart := time.Now()
		compiledC, err := rC.CompileModule(ctx, wasmBytes)
		if err != nil {
			panic(err)
		}
		tComp := float64(time.Since(tCompStart).Microseconds()) / 1000.0

		tInstStart := time.Now()
		mod, err := rC.InstantiateModule(ctx, compiledC, wazero.NewModuleConfig())
		if err != nil {
			panic(err)
		}
		tInst := float64(time.Since(tInstStart).Microseconds()) / 1000.0

		abi, err := analyzer.NewWazeroInstanceABI(mod)
		if err != nil {
			panic(err)
		}

		tCheckStart := time.Now()
		res := analyzer.CheckSourceWithABI(ctx, abi, testHyStr)
		if res.Verdict != analyzer.VerdictReject {
			panic("expected reject")
		}
		tCheck := float64(time.Since(tCheckStart).Microseconds()) / 1000.0

		_ = mod.Close(ctx)
		_ = rC.Close(ctx)
		tTotal := float64(time.Since(t0).Microseconds()) / 1000.0

		caseCCompile = append(caseCCompile, tComp)
		caseCInst = append(caseCInst, tInst)
		caseCCheck = append(caseCCheck, tCheck)
		caseCTotal = append(caseCTotal, tTotal)
	}
	_ = memCache.Close(ctx)
	sCComp := calcStats(caseCCompile)
	sCInst := calcStats(caseCInst)
	sCCheck := calcStats(caseCCheck)
	sCTotal := calcStats(caseCTotal)

	fmt.Printf("[CASE C] New Runtime (In-Memory Cache): total_mean=%.3fms (compile=%.3fms, inst=%.3fms, check=%.3fms)\n",
		sCTotal.Mean, sCComp.Mean, sCInst.Mean, sCCheck.Mean)
	fmt.Printf("         Compile Stats: min=%.3fms, p50=%.3fms, p90=%.3fms, p99=%.3fms, mean=%.3fms\n",
		sCComp.Min, sCComp.P50, sCComp.P90, sCComp.P99, sCComp.Mean)

	// CASE D: Filesystem compilation cache
	var caseDCompile, caseDInst, caseDCheck, caseDTotal []float64
	cacheDir := filepath.Join(os.TempDir(), "wazero-cache-test")
	_ = os.RemoveAll(cacheDir)
	_ = os.MkdirAll(cacheDir, 0755)
	defer os.RemoveAll(cacheDir)

	fsCache, err := wazero.NewCompilationCacheWithDir(cacheDir)
	if err != nil {
		fmt.Printf("[CASE D] Filesystem cache NOT TESTED: %v\n", err)
	} else {
		// Warm fs cache once
		rWarmFS := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithCompilationCache(fsCache))
		cWarmFS, err := rWarmFS.CompileModule(ctx, wasmBytes)
		if err != nil {
			panic(err)
		}
		_ = cWarmFS.Close(ctx)
		_ = rWarmFS.Close(ctx)
		_ = fsCache.Close(ctx)

		
		for i := 0; i < iterations; i++ {
			t0 := time.Now()
			// Open new fs cache handle simulating new runtime process
			fsHandle, err := wazero.NewCompilationCacheWithDir(cacheDir)
			if err != nil {
				panic(err)
			}

			rD := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithCompilationCache(fsHandle))

			tCompStart := time.Now()
			compiledD, err := rD.CompileModule(ctx, wasmBytes)
			if err != nil {
				panic(err)
			}
			tComp := float64(time.Since(tCompStart).Microseconds()) / 1000.0

			tInstStart := time.Now()
			mod, err := rD.InstantiateModule(ctx, compiledD, wazero.NewModuleConfig())
			if err != nil {
				panic(err)
			}
			tInst := float64(time.Since(tInstStart).Microseconds()) / 1000.0

			abi, err := analyzer.NewWazeroInstanceABI(mod)
			if err != nil {
				panic(err)
			}

			tCheckStart := time.Now()
			res := analyzer.CheckSourceWithABI(ctx, abi, testHyStr)
			if res.Verdict != analyzer.VerdictReject {
				panic("expected reject")
			}
			tCheck := float64(time.Since(tCheckStart).Microseconds()) / 1000.0

			_ = mod.Close(ctx)
			_ = rD.Close(ctx)
			_ = fsHandle.Close(ctx)
			tTotal := float64(time.Since(t0).Microseconds()) / 1000.0

			caseDCompile = append(caseDCompile, tComp)
			caseDInst = append(caseDInst, tInst)
			caseDCheck = append(caseDCheck, tCheck)
			caseDTotal = append(caseDTotal, tTotal)
		}
		sDComp := calcStats(caseDCompile)
		sDInst := calcStats(caseDInst)
		sDCheck := calcStats(caseDCheck)
		sDTotal := calcStats(caseDTotal)

		fmt.Printf("[CASE D] New Runtime (Filesystem Cache): total_mean=%.3fms (compile=%.3fms, inst=%.3fms, check=%.3fms)\n",
			sDTotal.Mean, sDComp.Mean, sDInst.Mean, sDCheck.Mean)
		fmt.Printf("         Compile Stats: min=%.3fms, p50=%.3fms, p90=%.3fms, p99=%.3fms, mean=%.3fms\n",
			sDComp.Min, sDComp.P50, sDComp.P90, sDComp.P99, sDComp.Mean)
	}

	// CASE E: Already-compiled and instantiated module / repeated checks (warm check latency)
	rE := wazero.NewRuntime(ctx)
	compiledE, err := rE.CompileModule(ctx, wasmBytes)
	if err != nil {
		panic(err)
	}
	modE, err := rE.InstantiateModule(ctx, compiledE, wazero.NewModuleConfig())
	if err != nil {
		panic(err)
	}
	abiE, err := analyzer.NewWazeroInstanceABI(modE)
	if err != nil {
		panic(err)
	}

	var caseEWarm []float64
	for i := 0; i < iterations; i++ {
		t0 := time.Now()
		res := analyzer.CheckSourceWithABI(ctx, abiE, testHyStr)
		if res.Verdict != analyzer.VerdictReject {
			panic("expected reject")
		}
		caseEWarm = append(caseEWarm, float64(time.Since(t0).Microseconds())/1000.0)
	}
	_ = modE.Close(ctx)
	_ = rE.Close(ctx)
	sEWarm := calcStats(caseEWarm)

	// Break-Even Calculations:
	// Primary: p50-based comparison (robust to scheduler outliers)
	// Secondary: mean-based comparison (outlier-sensitive)

	savingsP50 := subprocStats.P50 - sEWarm.P50
	nColdP50 := sACompile.P50 / savingsP50

	savingsMean := subprocStats.Mean - sEWarm.Mean
	nColdMean := sACompile.Mean / savingsMean

	speedupP50 := subprocStats.P50 / sEWarm.P50
	speedupMean := subprocStats.Mean / sEWarm.Mean

	fmt.Println("================================================================================")
	fmt.Println("BREAK-EVEN QUANTITATIVE ANALYSIS (100 ITERATIONS CONFIRMATORY):")
	fmt.Printf(" [Primary: p50-based Metrics]\n")
	fmt.Printf("  • Native CLI Subprocess p50:                 %.3f ms\n", subprocStats.P50)
	fmt.Printf("  • Pure Warm WASM In-Process p50:             %.3f ms\n", sEWarm.P50)
	fmt.Printf("  • Representative Steady-State Speedup (p50): %.1fx\n", speedupP50)
	fmt.Printf("  • Latency Advantage per Check (delta_T p50): %.3f ms\n", savingsP50)
	fmt.Printf("  • Cold Compile Overhead (T_compile_cold p50):%.3f ms\n", sACompile.P50)
	fmt.Printf("  -> Break-Even (Cold / No Cache, p50):        N_break_even ≈ %.1f checks (ceil: %d checks)\n",
		nColdP50, int(math.Ceil(nColdP50)))

	if len(caseDCompile) > 0 {
		sDComp := calcStats(caseDCompile)
		nFSP50 := sDComp.P50 / savingsP50
		fmt.Printf("  • FS Cached Compile (T_compile_fs p50):      %.3f ms\n", sDComp.P50)
		fmt.Printf("  -> Break-Even (Filesystem Cache, p50):       N_break_even ≈ %.1f checks (ceil: %d checks)\n",
			nFSP50, int(math.Ceil(nFSP50)))
	}

	fmt.Println("\n [Secondary: Outlier-Sensitive Mean Metrics]")
	fmt.Printf("  • Native CLI Subprocess Mean:                %.3f ms\n", subprocStats.Mean)
	fmt.Printf("  • Pure Warm WASM In-Process Mean:            %.3f ms\n", sEWarm.Mean)
	fmt.Printf("  • Ratio of Means (Mean Speedup):             %.1fx\n", speedupMean)
	fmt.Printf("  -> Break-Even (Cold / No Cache, Mean):       N_break_even ≈ %.1f checks (ceil: %d checks)\n",
		nColdMean, int(math.Ceil(nColdMean)))
	if len(caseDCompile) > 0 {
		sDComp := calcStats(caseDCompile)
		nFSMean := sDComp.Mean / savingsMean
		fmt.Printf("  -> Break-Even (Filesystem Cache, Mean):      N_break_even ≈ %.1f checks (ceil: %d checks)\n",
			nFSMean, int(math.Ceil(nFSMean)))
	}
	fmt.Println("================================================================================")
}
