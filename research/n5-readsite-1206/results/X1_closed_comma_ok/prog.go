package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	ch := make(chan map[string]int, 1)
	close(ch)
	m, ok := <-ch
	if !ok {
		m["k"] = 1
	}
	fmt.Println(ok, len(m))
}
