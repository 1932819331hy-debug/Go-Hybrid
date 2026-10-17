package analyzer

import (
	"go/token"
	"path/filepath"
	"testing"

	"golang.org/x/tools/go/analysis"
	"golang.org/x/tools/go/packages"
)

func TestAnalyzerEmpiricalPassContext(t *testing.T) {
	testdata, err := filepath.Abs("testdata/src/samplepkg")
	if err != nil {
		t.Fatal(err)
	}

	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles |
			packages.NeedImports | packages.NeedTypes | packages.NeedTypesSizes |
			packages.NeedSyntax | packages.NeedTypesInfo,
		Dir: testdata,
	}

	pkgs, err := packages.Load(cfg, ".")
	if err != nil {
		t.Fatalf("failed to load packages: %v", err)
	}
	if len(pkgs) == 0 {
		t.Fatalf("no packages loaded")
	}

	pkg := pkgs[0]
	var reportedDiags []analysis.Diagnostic

	pass := &analysis.Pass{
		Analyzer:     Analyzer,
		Fset:         pkg.Fset,
		Files:        pkg.Syntax,
		OtherFiles:   pkg.OtherFiles,
		IgnoredFiles: pkg.IgnoredFiles,
		Pkg:          pkg.Types,
		TypesInfo:    pkg.TypesInfo,
		TypesSizes:   pkg.TypesSizes,
		Report: func(d analysis.Diagnostic) {
			reportedDiags = append(reportedDiags, d)
		},
	}

	_, err = run(pass)
	if err != nil {
		t.Fatalf("analyzer run failed: %v", err)
	}

	// 1. Empirical Question A: Do .hy files enter pass.Files?
	// Expectation: NO. Only Go AST files enter pass.Files.
	if len(pass.Files) != 1 {
		t.Errorf("Expected exactly 1 Go file in pass.Files, got %d", len(pass.Files))
	}
	t.Logf("A. pass.Files count: %d (Only .go files parsed into Go AST)", len(pass.Files))

	// 2. Empirical Question B: Can .hy be discovered via OtherFiles or IgnoredFiles?
	// Finding: In stock Go packages, OtherFiles and IgnoredFiles do NOT include .hy files.
	t.Logf("B1. pass.OtherFiles: %v (len=%d)", pass.OtherFiles, len(pass.OtherFiles))
	t.Logf("B2. pass.IgnoredFiles: %v (len=%d)", pass.IgnoredFiles, len(pass.IgnoredFiles))
	if len(pass.OtherFiles) > 0 {
		t.Errorf("Expected empty OtherFiles for non-Go files, got %v", pass.OtherFiles)
	}

	// 3. Sibling discovery requires directory scan anchor:
	t.Logf("C. Discovered .hy files via DirScan: %v", LastContextLog.FoundHyFiles)
	if len(LastContextLog.FoundHyFiles) == 0 {
		t.Errorf("Expected to discover sibling .hy file via directory scan")
	}

	// 4. Empirical Question D: Available Go context:
	t.Logf("D. PkgPath: %s, HasTypesInfo: %v", pass.Pkg.Path(), pass.TypesInfo != nil)

	// 5. Empirical Question E: Diagnostic reporting and source position contract:
	t.Logf("E. Reported %d diagnostic(s) from sibling .hy analysis:", len(reportedDiags))
	for _, d := range reportedDiags {
		position := pass.Fset.Position(d.Pos)
		t.Logf(" - [%s] Pos=%d (%s): %s", d.Category, d.Pos, position, d.Message)
		if position.Line == 0 || position.Column == 0 {
			t.Errorf("Expected valid Line and Column from exact position, got %s", position)
		}
	}
	if len(reportedDiags) == 0 {
		t.Errorf("Expected diagnostics from invalid .hy, got 0")
	}
}

func TestAnalyzerDirectInvocation(t *testing.T) {
	engine, err := GetEngine("")
	if err != nil {
		t.Fatalf("failed to get engine: %v", err)
	}

	badHy := `// EXPECTED-VERDICT: reject HYB-E3003
package samplepkg

fn bad_use_after_move() {
    let x = 42
    let y = move x
    let z = x
}
`
	out, err := engine.Check(badHy)
	if err != nil {
		t.Fatalf("WASM engine check failed: %v", err)
	}

	if out.Summary.Verdict != "reject" {
		t.Fatalf("Expected verdict reject, got %s", out.Summary.Verdict)
	}

	var diags []analysis.Diagnostic
	pass := &analysis.Pass{
		Fset: token.NewFileSet(),
		Report: func(d analysis.Diagnostic) {
			diags = append(diags, d)
		},
	}

	// Map to analysis.Diagnostic
	for _, d := range out.Diagnostics {
		pass.Report(analysis.Diagnostic{
			Pos:      token.NoPos,
			Message:  d.Message,
			Category: d.Code,
		})
	}

	if len(diags) == 0 {
		t.Fatalf("Expected reported diagnostics, got 0")
	}

	t.Logf("Successfully mapped %d diagnostics to analysis.Pass:", len(diags))
	for _, d := range diags {
		t.Logf(" - [%s] %s", d.Category, d.Message)
	}
}
