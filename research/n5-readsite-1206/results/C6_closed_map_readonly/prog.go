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
	a, oka := <-ch
	b, okb := <-ch
	fmt.Println(oka, okb, len(a), len(b))
}
