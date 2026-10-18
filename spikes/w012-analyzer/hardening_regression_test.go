package analyzer

import (
	"os"
	"testing"
)

func TestAcceptInvariantAllowsWarningsButNotErrors(t *testing.T) {
	warning := Diagnostic{
		Code:     "HYB-E8001",
		Severity: "warning",
		Message:  "managed escape hatch used",
		Span: Span{
			Start: Pos{Line: 1, Col: 1},
			End:   Pos{Line: 1, Col: 2},
		},
	}
	accept := &AnalysisResult{Verdict: VerdictAccept, Diagnostics: []Diagnostic{warning}}
	if err := accept.CheckInvariant(); err != nil {
		t.Fatalf("accept+warning must be valid: %v", err)
	}

	errorDiag := warning
	errorDiag.Code = "HYB-E3003"
	errorDiag.Severity = "error"
	bad := &AnalysisResult{Verdict: VerdictAccept, Diagnostics: []Diagnostic{errorDiag}}
	if err := bad.CheckInvariant(); err == nil {
		t.Fatal("accept+error must violate the verdict invariant")
	}
}

func TestRuneColumnByteOffset(t *testing.T) {
	src := "αβx\nplain"
	cases := []struct {
		line int
		col  int
		want int
	}{
		{1, 1, 0},
		{1, 2, len("α")},
		{1, 3, len("αβ")},
		{1, 4, len("αβx")},
		{2, 3, len("pl")},
	}
	for _, tc := range cases {
		got, err := runeColumnByteOffset(src, tc.line, tc.col)
		if err != nil {
			t.Fatalf("%d:%d: %v", tc.line, tc.col, err)
		}
		if got != tc.want {
			t.Fatalf("%d:%d: byte offset=%d, want %d", tc.line, tc.col, got, tc.want)
		}
	}
	if _, err := runeColumnByteOffset(src, 1, 99); err == nil {
		t.Fatal("out-of-range Unicode column must fail closed")
	}
}

// Requires a freshly rebuilt hy_wasm.wasm from this branch. It intentionally
// proves two contracts at once: the historical/default ABI keeps managed data
// omitted for native/WASM parity, while the opt-in ABI preserves both warning
// diagnostics and the complete managed audit.
func TestManagedWarningsAndAuditPreserved(t *testing.T) {
	src, err := os.ReadFile("../../tests/checker-corpus/02-shared-map-managed.hy")
	if err != nil {
		t.Fatal(err)
	}
	engine, err := GetEngine("")
	if err != nil {
		t.Fatalf("load fresh WASM artifact: %v", err)
	}

	plain, err := engine.Check(string(src))
	if err != nil {
		t.Fatalf("default check: %v", err)
	}
	if plain.Summary.Verdict != "accept" || plain.Summary.Errors != 0 {
		t.Fatalf("managed corpus must accept: %+v", plain.Summary)
	}
	if plain.Summary.Warnings == 0 || len(plain.Diagnostics) == 0 {
		t.Fatalf("accept warnings were dropped: summary=%+v diagnostics=%+v", plain.Summary, plain.Diagnostics)
	}
	if plain.Summary.Managed != nil {
		t.Fatalf("default ABI must preserve historical parity and omit managed report: %+v", plain.Summary.Managed)
	}

	managed, err := engine.CheckManaged(string(src))
	if err != nil {
		t.Fatalf("managed check (rebuild hy_wasm.wasm from this branch): %v", err)
	}
	if managed.Summary.Verdict != "accept" || managed.Summary.Errors != 0 {
		t.Fatalf("managed-report check changed verdict: %+v", managed.Summary)
	}
	if managed.Summary.Warnings == 0 || len(managed.Diagnostics) == 0 {
		t.Fatalf("managed-report check dropped warnings: summary=%+v diagnostics=%+v", managed.Summary, managed.Diagnostics)
	}
	if managed.Summary.Managed == nil {
		t.Fatal("managed-report ABI omitted audit payload")
	}
	if managed.Summary.Managed.ManagedBlocks < 1 || len(managed.Summary.Managed.Blocks) < 1 {
		t.Fatalf("managed audit is incomplete: %+v", managed.Summary.Managed)
	}
	first := managed.Summary.Managed.Blocks[0]
	if first.Reason == "" || first.Category == "" {
		t.Fatalf("managed block lost reason/category: %+v", first)
	}
}
