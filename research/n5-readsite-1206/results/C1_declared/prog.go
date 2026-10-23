package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	var m map[string]int = map[string]int{}
	var b bag = bag{counts: map[string]int{}}
	p := &bag{counts: map[string]int{}}
	m["k"] = 1
	b.counts["k"] = 2
	p.counts["k"] = 3
	fmt.Println(m["k"], b.counts["k"], p.counts["k"])
}
