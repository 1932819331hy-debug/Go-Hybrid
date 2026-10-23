package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	gs := map[string]bag{}
	g := gs["x"]
	g.counts["k"] = 10
	fmt.Println(len(gs), len(g.counts))
}
