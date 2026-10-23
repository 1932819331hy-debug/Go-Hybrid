package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	mm := map[string]map[string]int{}
	m, ok := mm["x"]
	fmt.Println(ok, len(mm), len(m))
}
