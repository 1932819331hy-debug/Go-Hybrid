package analyzer

import (
	"context"
	"fmt"
	"go/token"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"sync"
	"time"

	"github.com/tetratelabs/wazero"
	"golang.org/x/tools/go/analysis"
)

type Pos struct {
	Line int `json:"line"`
	Col  int `json:"col"`
}

type Span struct {
	Start Pos `json:"start"`
	End   Pos `json:"end"`
}

type Diagnostic struct {
	Code      string `json:"code"`
	Severity  string `json:"severity"`
	Message   string `json:"message"`
	Span      Span   `json:"span"`
	Tier0Rule string `json:"tier0_rule,omitempty"`
}

type Summary struct {
	File     string `json:"file"`
	Verdict  string `json:"verdict"`
	Errors   int    `json:"errors"`
	Warnings int    `json:"warnings"`
}

type CheckOutput struct {
	Diagnostics []Diagnostic `json:"diagnostics"`
	Summary     Summary      `json:"summary"`
	Version     int          `json:"version"`
}

type Engine struct {
	ctx      context.Context
	runtime  wazero.Runtime
	compiled wazero.CompiledModule
	mod      wazero.CompiledModule // keep ref
}

var (
	defaultWasmPath  = wasmArtifactPath()
	globalEngine     *Engine
	globalEngineOnce sync.Once
	globalEngineErr  error
)

func wasmArtifactPath() string {
	if path := os.Getenv("HYBRID_WASM_PATH"); path != "" {
		return path
	}
	return filepath.Join("..", "..", "compiler", "target", "wasm32-unknown-unknown", "release", "hy_wasm.wasm")
}

func GetEngine(wasmPath string) (*Engine, error) {
	globalEngineOnce.Do(func() {
		if wasmPath == "" {
			wasmPath = defaultWasmPath
		}
		wasmBytes, err := os.ReadFile(wasmPath)
		if err != nil {
			globalEngineErr = fmt.Errorf("failed to read wasm binary: %w", err)
			return
		}
		ctx := context.Background()
		r := wazero.NewRuntimeWithConfig(ctx, wazero.NewRuntimeConfig().WithCloseOnContextDone(true))
		compiled, err := r.CompileModule(ctx, wasmBytes)
		if err != nil {
			r.Close(ctx)
			globalEngineErr = fmt.Errorf("failed to compile wasm module: %w", err)
			return
		}
		globalEngine = &Engine{
			ctx:      ctx,
			runtime:  r,
			compiled: compiled,
		}
	})
	return globalEngine, globalEngineErr
}

// Check bounds a single analysis even when the go/analysis driver does not
// provide a cancellation context. Infrastructure timeout remains fail-closed.
func (e *Engine) Check(src string) (*CheckOutput, error) {
	ctx, cancel := context.WithTimeout(e.ctx, 30*time.Second)
	defer cancel()
	return e.CheckContext(ctx, src)
}

// CheckContext permits an IDE or other embedding host to cancel one request
// without closing the shared wazero runtime or poisoning later checks.
func (e *Engine) CheckContext(ctx context.Context, src string) (*CheckOutput, error) {
	if ctx == nil {
		return nil, fmt.Errorf("nil analysis context")
	}
	engine := &WasmEngine{ctx: e.ctx, runtime: e.runtime, compiled: e.compiled}
	res := engine.CheckSource(ctx, src)
	if res.Verdict == VerdictAnalysisFailure {
		return nil, res.Failure
	}
	return &CheckOutput{Version: 1, Summary: *res.Summary, Diagnostics: res.Diagnostics}, nil
}

// PassContextLog records empirical pass inspection for research
type PassContextLog struct {
	PkgPath            string
	FilesCount         int
	GoFiles            []string
	OtherFiles         []string
	IgnoredFiles       []string
	HasReadFile        bool
	HasTypesInfo       bool
	FoundHyFiles       []string
	PassReadFileTested bool
	PassReadFileErr    string
	PassReadFileLen    int
}

var (
	lastContextLogMu sync.RWMutex
	lastContextLog   PassContextLog
)

// Clone the slices as well as the struct: copying only its slice headers would
// expose mutable internal arrays to callers even while protecting assignments
// to lastContextLog with a mutex.
func clonePassContextLog(log PassContextLog) PassContextLog {
	log.GoFiles = slices.Clone(log.GoFiles)
	log.OtherFiles = slices.Clone(log.OtherFiles)
	log.IgnoredFiles = slices.Clone(log.IgnoredFiles)
	log.FoundHyFiles = slices.Clone(log.FoundHyFiles)
	return log
}

func setLastContextLog(log PassContextLog) {
	lastContextLogMu.Lock()
	lastContextLog = clonePassContextLog(log)
	lastContextLogMu.Unlock()
}

// LastContextLogSnapshot returns an independent snapshot. Callers may modify
// its slices without racing with later analyzer runs or other snapshots.
func LastContextLogSnapshot() PassContextLog {
	lastContextLogMu.RLock()
	defer lastContextLogMu.RUnlock()
	return clonePassContextLog(lastContextLog)
}

var Analyzer = &analysis.Analyzer{
	Name: "hybrid",
	Doc:  "Spike: inspects Go package and invokes in-process WASM checker on sibling .hy files",
	Run:  run,
}

func run(pass *analysis.Pass) (interface{}, error) {
	log := PassContextLog{
		PkgPath:      pass.Pkg.Path(),
		FilesCount:   len(pass.Files),
		OtherFiles:   pass.OtherFiles,
		IgnoredFiles: pass.IgnoredFiles,
		HasReadFile:  pass.ReadFile != nil,
		HasTypesInfo: pass.TypesInfo != nil,
	}

	for _, f := range pass.Files {
		if pass.Fset != nil {
			pos := pass.Fset.File(f.Pos())
			if pos != nil {
				log.GoFiles = append(log.GoFiles, pos.Name())
			}
		}
	}

	engine, err := GetEngine("")
	if err != nil {
		return nil, fmt.Errorf("failed to init wazero engine: %w", err)
	}

	// Sibling .hy discovery:
	// A. Check pass.OtherFiles
	// B. Check directory containing Go files
	hyFiles := make(map[string]string) // name -> full path or content

	// Declared files are a trusted input set, not an optional hint: failing to
	// read one must fail this analysis, never silently produce an all-clear.
	for _, of := range pass.OtherFiles {
		if filepath.Ext(of) != ".hy" {
			continue
		}
		log.FoundHyFiles = append(log.FoundHyFiles, "OtherFiles:"+of)
		var data []byte
		var readErr error
		if pass.ReadFile != nil {
			data, readErr = pass.ReadFile(of)
		} else {
			data, readErr = os.ReadFile(of)
		}
		if readErr != nil {
			return nil, fmt.Errorf("read declared .hy file %q: %w", of, readErr)
		}
		name, err := filepath.Abs(of)
		if err != nil {
			return nil, fmt.Errorf("resolve declared .hy path %q: %w", of, err)
		}
		if previous, exists := hyFiles[name]; exists && previous != string(data) {
			return nil, fmt.Errorf("conflicting content for declared .hy file %q", name)
		}
		hyFiles[name] = string(data)
	}

	// Also inspect disk directory of first Go file to test if sibling .hy can be discovered via dir scan
	if len(pass.Files) > 0 && pass.Fset != nil {
		firstFile := pass.Fset.File(pass.Files[0].Pos())
		if firstFile != nil {
			dir := filepath.Dir(firstFile.Name())
			entries, err := os.ReadDir(dir)
			if err != nil {
				return nil, fmt.Errorf("discover .hy files: %w", err)
			}
			for _, e := range entries {
				if filepath.Ext(e.Name()) == ".hy" {
					fullPath, err := filepath.Abs(filepath.Join(dir, e.Name()))
					if err != nil {
						return nil, fmt.Errorf("resolve sibling .hy path %q: %w", e.Name(), err)
					}
					if _, already := hyFiles[fullPath]; !already {
						log.FoundHyFiles = append(log.FoundHyFiles, "DirScan:"+e.Name())

						// Test pass.ReadFile contract on this unlisted sibling .hy file:
						if pass.ReadFile != nil {
							rfData, rfErr := pass.ReadFile(fullPath)
							log.PassReadFileTested = true
							if rfErr != nil {
								log.PassReadFileErr = rfErr.Error()
							} else {
								log.PassReadFileLen = len(rfData)
							}
						}

						// Primary read via os.ReadFile to support on-disk singlechecker execution
						data, err := os.ReadFile(fullPath)
						if err != nil {
							return nil, fmt.Errorf("read %s: %w", fullPath, err)
						}
						hyFiles[fullPath] = string(data)
					}
				}
			}
		}
	}

	setLastContextLog(log)

	// Map iteration order is unstable: report diagnostics in canonical filename
	// order so identical source trees yield reproducible go vet output.
	filenames := make([]string, 0, len(hyFiles))
	for filename := range hyFiles {
		filenames = append(filenames, filename)
	}
	sort.Strings(filenames)
	for _, filename := range filenames {
		content := hyFiles[filename]
		out, err := engine.Check(content)
		if err != nil {
			return nil, fmt.Errorf("hybrid check on %s failed: %w", filename, err)
		}

		var fileToken *token.File
		if pass.Fset != nil {
			fileToken = pass.Fset.AddFile(filename, -1, len(content))
			fileToken.SetLinesForContent([]byte(content))
		}

		// Acceptance means no errors, not no diagnostics: retain managed warnings.
		for _, d := range out.Diagnostics {
			var exactPos token.Pos = token.NoPos
			if fileToken != nil && d.Span.Start.Line > 0 {
				if d.Span.Start.Line > fileToken.LineCount() {
					return nil, fmt.Errorf("diagnostic line outside %s", filename)
				}
				lineStart := fileToken.LineStart(d.Span.Start.Line)
				if lineStart.IsValid() {
					exactPos = lineStart + token.Pos(d.Span.Start.Col-1)
				}
			}

			pass.Report(analysis.Diagnostic{
				Pos:      exactPos,
				Message:  fmt.Sprintf("[%s] %s", d.Code, d.Message),
				Category: "hybrid",
			})
		}
	}

	return nil, nil
}
