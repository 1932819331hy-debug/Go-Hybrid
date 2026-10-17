package main

import (
	analyzer "github.com/limitlessprojects/go-hybrid/spikes/w012-analyzer"
	"golang.org/x/tools/go/analysis/singlechecker"
)

func main() {
	singlechecker.Main(analyzer.Analyzer)
}
