package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	mm := map[string]map[string]int{}
	mm["a"]["b"] = 1
	fmt.Println(len(mm))
}
