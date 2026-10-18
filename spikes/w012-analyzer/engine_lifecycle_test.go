package analyzer

import (
	"context"
	"fmt"
	"os"
	"sync"
	"testing"
)

// This is a lifetime/state-drift stress test, not a heap-leak proof. Each
// CheckSource call instantiates and closes a disposable guest module while
// reusing the compiled module/runtime. A successful 20k run demonstrates that
// the ownership protocol survives sustained use without verdict drift or an
// obvious guest-lifecycle failure; RSS/heap stability still requires separate
// measurement.
func TestDisposableGuestLifecycle20000(t *testing.T) {
	wasmBytes, err := os.ReadFile(defaultWasmPath)
	if err != nil {
		t.Fatalf("fresh WASM artifact required: %v", err)
	}
	engine, err := NewWasmEngine(context.Background(), wasmBytes)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()

	sourceBytes, err := os.ReadFile("../../tests/checker-corpus/09-a3-map-slot-valid.hy")
	if err != nil {
		t.Fatal(err)
	}
	source := string(sourceBytes)
	ctx := context.Background()

	const iterations = 20000
	for i := 0; i < iterations; i++ {
		res := engine.CheckSource(ctx, source)
		if res.Verdict != VerdictAccept || res.Failure != nil {
			t.Fatalf("iteration %d drifted: verdict=%s failure=%v diagnostics=%+v", i, res.Verdict, res.Failure, res.Diagnostics)
		}
		if err := res.CheckInvariant(); err != nil {
			t.Fatalf("iteration %d invariant: %v", i, err)
		}
	}
}

func TestConcurrentDisposableGuestChecks(t *testing.T) {
	wasmBytes, err := os.ReadFile(defaultWasmPath)
	if err != nil {
		t.Fatalf("fresh WASM artifact required: %v", err)
	}
	engine, err := NewWasmEngine(context.Background(), wasmBytes)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()

	acceptBytes, err := os.ReadFile("../../tests/checker-corpus/09-a3-map-slot-valid.hy")
	if err != nil {
		t.Fatal(err)
	}
	rejectBytes, err := os.ReadFile("../../tests/checker-corpus/08-a3-map-slot-invalidated.hy")
	if err != nil {
		t.Fatal(err)
	}
	acceptSrc := string(acceptBytes)
	rejectSrc := string(rejectBytes)

	const workers = 8
	const iterationsPerWorker = 200
	var wg sync.WaitGroup
	errCh := make(chan error, workers)
	for worker := 0; worker < workers; worker++ {
		wg.Add(1)
		go func(worker int) {
			defer wg.Done()
			ctx := context.Background()
			for i := 0; i < iterationsPerWorker; i++ {
				accept := engine.CheckSource(ctx, acceptSrc)
				if accept.Verdict != VerdictAccept || accept.Failure != nil {
					errCh <- fmt.Errorf("worker %d iteration %d accept drift: verdict=%s failure=%v", worker, i, accept.Verdict, accept.Failure)
					return
				}
				reject := engine.CheckSource(ctx, rejectSrc)
				if reject.Verdict != VerdictReject || reject.Failure != nil {
					errCh <- fmt.Errorf("worker %d iteration %d reject drift: verdict=%s failure=%v", worker, i, reject.Verdict, reject.Failure)
					return
				}
				if err := reject.CheckInvariant(); err != nil {
					errCh <- fmt.Errorf("worker %d iteration %d reject invariant: %w", worker, i, err)
					return
				}
			}
		}(worker)
	}
	wg.Wait()
	close(errCh)
	for err := range errCh {
		t.Fatal(err)
	}
}
