package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	mm := map[string]map[string]int{"x": map[string]int{"k": 1}}
	a := mm["x"]
	b, ok := mm["x"]
	a["k"] = 2
	fmt.Println(mm["x"]["k"], b["k"], ok, len(mm))
}
