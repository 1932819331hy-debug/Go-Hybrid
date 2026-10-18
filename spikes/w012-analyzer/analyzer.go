package analyzer

import (
	"context"
	"fmt"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"sync"

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

type ManagedBlockAudit struct {
	Reason   string   `json:"reason"`
	Category string   `json:"category"`
	Tickets  []string `json:"tickets,omitempty"`
}

type ManagedReport struct {
	ManagedBlocks   int                 `json:"managed_blocks"`
	SitesViaManaged int                 `json:"sites_via_managed"`
	TotalSites      int                 `json:"total_sites"`
	Categories      map[string]int      `json:"categories,omitempty"`
	Blocks          []ManagedBlockAudit `json:"blocks,omitempty"`
}

type Summary struct {
	File     string         `json:"file"`
	Verdict  string         `json:"verdict"`
	Errors   int            `json:"errors"`
	Warnings int            `json:"warnings"`
	Managed  *ManagedReport `json:"managed,omitempty"`
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

func (e *Engine) Check(src string) (*CheckOutput, error) {
	engine := &WasmEngine{ctx: e.ctx, runtime: e.runtime, compiled: e.compiled}
	res := engine.CheckSource(e.ctx, src)
	if res.Verdict == VerdictAnalysisFailure {
		return nil, res.Failure
	}
	return &CheckOutput{Version: 1, Summary: *res.Summary, Diagnostics: res.Diagnostics}, nil
}

func (e *Engine) CheckManaged(src string) (*CheckOutput, error) {
	engine := &WasmEngine{ctx: e.ctx, runtime: e.runtime, compiled: e.compiled}
	res := engine.CheckSourceManaged(e.ctx, src)
	if res.Verdict == VerdictAnalysisFailure {
		return nil, res.Failure
	}
	return &CheckOutput{Version: 1, Summary: *res.Summary, Diagnostics: res.Diagnostics}, nil
}

// PassContextLog records empirical pass inspection for research.
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

func setLastContextLog(log PassContextLog) {
	lastContextLogMu.Lock()
	lastContextLog = log
	lastContextLogMu.Unlock()
}

// LastContextLogSnapshot returns the latest research observation without
// exposing mutable shared state to concurrent analyzer runs.
func LastContextLogSnapshot() PassContextLog {
	lastContextLogMu.RLock()
	defer lastContextLogMu.RUnlock()
	return lastContextLog
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

	// Check OtherFiles
	for _, of := range pass.OtherFiles {
		if filepath.Ext(of) == ".hy" {
			log.FoundHyFiles = append(log.FoundHyFiles, "OtherFiles:"+of)
			if pass.ReadFile != nil {
				data, err := pass.ReadFile(of)
				if err == nil {
					hyFiles[of] = string(data)
				}
			}
		}
	}

	// Also inspect disk directory of first Go file to test if sibling .hy can be discovered via dir scan.
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
					fullPath := filepath.Join(dir, e.Name())
					if _, already := hyFiles[fullPath]; !already {
						log.FoundHyFiles = append(log.FoundHyFiles, "DirScan:"+e.Name())

						// Test pass.ReadFile contract on this unlisted sibling .hy file.
						if pass.ReadFile != nil {
							rfData, rfErr := pass.ReadFile(fullPath)
							log.PassReadFileTested = true
							if rfErr != nil {
								log.PassReadFileErr = rfErr.Error()
							} else {
								log.PassReadFileLen = len(rfData)
							}
						}

						// Primary read via os.ReadFile to support on-disk singlechecker execution.
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

	// Run Hybrid checks on found .hy files.
	for filename, content := range hyFiles {
		out, err := engine.Check(content)
		if err != nil {
			return nil, fmt.Errorf("hybrid check on %s failed: %w", filename, err)
		}

		var fileToken *token.File
		if pass.Fset != nil {
			fileToken = pass.Fset.AddFile(filename, -1, len(content))
			fileToken.SetLinesForContent([]byte(content))
		}

		if out.Summary.Verdict == "reject" {
			for _, d := range out.Diagnostics {
				// analysis.Diagnostic has no severity channel and vet treats
				// reported diagnostics as findings. E8xxx warnings must never
				// fail a build, so this adapter reports only hard errors.
				if d.Severity != "error" {
					continue
				}

				var exactPos token.Pos = token.NoPos
				if fileToken != nil && d.Span.Start.Line > 0 {
					if d.Span.Start.Line > fileToken.LineCount() {
						return nil, fmt.Errorf("diagnostic line outside %s", filename)
					}
					lineStart := fileToken.LineStart(d.Span.Start.Line)
					if lineStart.IsValid() {
						byteOffset, err := runeColumnByteOffset(content, d.Span.Start.Line, d.Span.Start.Col)
						if err != nil {
							return nil, fmt.Errorf("diagnostic position outside %s: %w", filename, err)
						}
						exactPos = lineStart + token.Pos(byteOffset)
					}
				}

				pass.Report(analysis.Diagnostic{
					Pos:      exactPos,
					Message:  fmt.Sprintf("[%s] %s", d.Code, d.Message),
					Category: "hybrid",
				})
			}
		}
	}

	return nil, nil
}

// Hybrid source positions are 1-based Unicode-scalar columns because the
// Rust lexer advances over `char`. go/token positions are byte offsets. This
// conversion keeps ASCII behavior identical while mapping non-ASCII prefixes
// to the correct token.Pos.
func runeColumnByteOffset(content string, line, col int) (int, error) {
	if line < 1 || col < 1 {
		return 0, fmt.Errorf("invalid line/column %d:%d", line, col)
	}
	lines := strings.Split(content, "\n")
	if line > len(lines) {
		return 0, fmt.Errorf("line %d exceeds %d lines", line, len(lines))
	}
	runes := []rune(lines[line-1])
	runeOffset := col - 1
	if runeOffset > len(runes) {
		return 0, fmt.Errorf("column %d exceeds line width %d", col, len(runes)+1)
	}
	return len(string(runes[:runeOffset])), nil
}
