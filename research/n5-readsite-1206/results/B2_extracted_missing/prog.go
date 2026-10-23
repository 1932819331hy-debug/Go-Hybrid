package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	mm := map[string]map[string]int{}
	inner := mm["x"]
	inner["k"] = 1
	fmt.Println(len(mm), len(inner))
}
