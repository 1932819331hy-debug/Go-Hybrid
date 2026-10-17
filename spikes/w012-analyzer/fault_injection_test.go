package analyzer

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

type MockWasmABI struct {
	AllocFn       func(ctx context.Context, byteLen uint64) (uint32, error)
	FreeFn        func(ctx context.Context, ptr, byteLen uint64) error
	CheckFn       func(ctx context.Context, ptr, byteLen uint64) (uint64, error)
	Memory        []byte
	FailRead      bool
	FailWrite     bool
}

func (m *MockWasmABI) Alloc(ctx context.Context, byteLen uint64) (uint32, error) {
	if m.AllocFn != nil {
		return m.AllocFn(ctx, byteLen)
	}
	return 0, nil
}

func (m *MockWasmABI) Free(ctx context.Context, ptr, byteLen uint64) error {
	if m.FreeFn != nil {
		return m.FreeFn(ctx, ptr, byteLen)
	}
	return nil
}

func (m *MockWasmABI) Check(ctx context.Context, ptr, byteLen uint64) (uint64, error) {
	if m.CheckFn != nil {
		return m.CheckFn(ctx, ptr, byteLen)
	}
	return 0, nil
}

func (m *MockWasmABI) WriteMemory(offset uint32, b []byte) bool {
	if m.FailWrite {
		return false
	}
	if int(offset)+len(b) > len(m.Memory) {
		return false
	}
	copy(m.Memory[offset:], b)
	return true
}

func (m *MockWasmABI) ReadMemory(offset, length uint32) ([]byte, bool) {
	if m.FailRead {
		return nil, false
	}
	if int(offset)+int(length) > len(m.Memory) {
		return nil, false
	}
	return m.Memory[offset : offset+length], true
}

func TestFailureDomainSeparation(t *testing.T) {
	ctx := context.Background()
	wasmBytes, err := os.ReadFile(defaultWasmPath)
	if err != nil {
		t.Fatalf("failed to read wasm binary: %v", err)
	}

	engine, err := NewWasmEngine(ctx, wasmBytes)
	if err != nil {
		t.Fatalf("failed to init engine: %v", err)
	}
	defer engine.Close()

	// 1. Positive Control: Valid Program -> VerdictAccept
	validSrcBytes, err := os.ReadFile("/Users/pro/Go-Hybrid/tests/checker-corpus/09-a3-map-slot-valid.hy")
	if err != nil {
		t.Fatalf("failed to read valid corpus file: %v", err)
	}
	resValid := engine.CheckSource(ctx, string(validSrcBytes))
	if err := resValid.CheckInvariant(); err != nil {
		t.Fatal(err)
	}
	if resValid.Verdict != VerdictAccept {
		t.Fatalf("Expected VerdictAccept, got %v (%+v)", resValid.Verdict, resValid.Diagnostics)
	}
	t.Logf("[PASS] Valid program -> %s", resValid.Verdict)

	// 2. Negative Control: Hybrid Rule Violation -> VerdictReject (User rejection)
	invalidSrcBytes, err := os.ReadFile("/Users/pro/Go-Hybrid/tests/checker-corpus/08-a3-map-slot-invalidated.hy")
	if err != nil {
		t.Fatalf("failed to read invalid corpus file: %v", err)
	}
	resInvalid := engine.CheckSource(ctx, string(invalidSrcBytes))
	if err := resInvalid.CheckInvariant(); err != nil {
		t.Fatal(err)
	}
	if resInvalid.Verdict != VerdictReject {
		t.Fatalf("Expected VerdictReject, got %v", resInvalid.Verdict)
	}
	if len(resInvalid.Diagnostics) == 0 {
		t.Fatalf("Expected diagnostics on reject, got 0")
	}
	t.Logf("[PASS] Rule violation -> %s (%d diagnostics)", resInvalid.Verdict, len(resInvalid.Diagnostics))

	// 3. Fault Injection 1: WASM Trap / Panic during execution
	t.Run("Fault_WasmTrap", func(t *testing.T) {
		mock := &MockWasmABI{
			Memory: make([]byte, 1024),
			CheckFn: func(ctx context.Context, ptr, byteLen uint64) (uint64, error) {
				return 0, errors.New("wasm trap: unreachable instruction executed")
			},
		}

		res := CheckSourceWithABI(ctx, mock, "struct test {}")
		if err := res.CheckInvariant(); err != nil {
			t.Fatal(err)
		}
		if res.Verdict != VerdictAnalysisFailure {
			t.Fatalf("Expected VerdictAnalysisFailure on WASM trap, got %v", res.Verdict)
		}
		t.Logf("[PASS] Fault Injection 1 (WASM Trap) -> %s: %v", res.Verdict, res.Failure)
	})

	// 4. Fault Injection 2: Malformed JSON diagnostic envelope
	t.Run("Fault_MalformedJSON", func(t *testing.T) {
		corrupt := []byte(`{"diagnostics": [{"code": "HYB-E3003", "severity": TRUNCATED_MALFORMED...`)
		mock := &MockWasmABI{
			Memory: make([]byte, 1024),
			CheckFn: func(ctx context.Context, ptr, byteLen uint64) (uint64, error) {
				return (uint64(64) << 32) | uint64(len(corrupt)), nil
			},
		}
		copy(mock.Memory[64:], corrupt)

		res := CheckSourceWithABI(ctx, mock, "struct test {}")
		if err := res.CheckInvariant(); err != nil {
			t.Fatal(err)
		}
		if res.Verdict != VerdictAnalysisFailure {
			t.Fatalf("Expected VerdictAnalysisFailure on malformed JSON, got %v", res.Verdict)
		}
		t.Logf("[PASS] Fault Injection 2 (Malformed JSON) -> %s: %v", res.Verdict, res.Failure)
	})

	// 5. Fault Injection 3: Invalid pointer / memory out-of-bounds
	t.Run("Fault_InvalidPointerBounds", func(t *testing.T) {
		mock := &MockWasmABI{
			Memory: make([]byte, 1024),
			CheckFn: func(ctx context.Context, ptr, byteLen uint64) (uint64, error) {
				// 50000 exceeds 1024-byte memory
				return (uint64(50000) << 32) | uint64(100), nil
			},
		}

		res := CheckSourceWithABI(ctx, mock, "struct test {}")
		if err := res.CheckInvariant(); err != nil {
			t.Fatal(err)
		}
		if res.Verdict != VerdictAnalysisFailure {
			t.Fatalf("Expected VerdictAnalysisFailure on invalid bounds, got %v", res.Verdict)
		}
		t.Logf("[PASS] Fault Injection 3 (Invalid Memory Bounds) -> %s: %v", res.Verdict, res.Failure)
	})

	// 6. Fault Injection 4: Host allocation failure
	t.Run("Fault_AllocFailure", func(t *testing.T) {
		mock := &MockWasmABI{
			Memory: make([]byte, 1024),
			AllocFn: func(ctx context.Context, byteLen uint64) (uint32, error) {
				return 0, errors.New("out of memory in guest wasm allocator")
			},
		}

		res := CheckSourceWithABI(ctx, mock, "struct test {}")
		if err := res.CheckInvariant(); err != nil {
			t.Fatal(err)
		}
		if res.Verdict != VerdictAnalysisFailure {
			t.Fatalf("Expected VerdictAnalysisFailure on alloc failure, got %v", res.Verdict)
		}
		t.Logf("[PASS] Fault Injection 4 (Alloc Failure) -> %s: %v", res.Verdict, res.Failure)
	})

	// 7. Fault Injection 5: Execution Timeout
	t.Run("Fault_Timeout", func(t *testing.T) {
		timeoutCtx, cancel := context.WithTimeout(ctx, 10*time.Millisecond)
		defer cancel()
		time.Sleep(15 * time.Millisecond) // expire context

		res := engine.CheckSource(timeoutCtx, "struct test {}")
		if err := res.CheckInvariant(); err != nil {
			t.Fatal(err)
		}
		if res.Verdict != VerdictAnalysisFailure {
			t.Fatalf("Expected VerdictAnalysisFailure on timeout, got %v", res.Verdict)
		}
		t.Logf("[PASS] Fault Injection 5 (Execution Timeout) -> %s: %v", res.Verdict, res.Failure)
	})
}
