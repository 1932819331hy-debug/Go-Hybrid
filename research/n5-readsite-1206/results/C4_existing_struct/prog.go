package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	bags := map[string]bag{"x": bag{counts: map[string]int{}}}
	b, ok := bags["x"]
	b.counts["k"] = 4
	fmt.Println(bags["x"].counts["k"], ok, len(bags))
}
