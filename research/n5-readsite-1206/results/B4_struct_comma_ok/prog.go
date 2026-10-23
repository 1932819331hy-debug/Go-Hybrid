package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	bags := map[string]bag{}
	b, ok := bags["x"]
	if !ok {
		b.counts["k"] = 1
	}
	fmt.Println(ok, len(bags), len(b.counts))
}
