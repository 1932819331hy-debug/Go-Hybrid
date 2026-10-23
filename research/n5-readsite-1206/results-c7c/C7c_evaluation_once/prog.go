package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func key(c map[string]int) string {
	c["outer"] += 1
	return "x"
}

func valuekey(c map[string]int) string {
	c["inner"] += 1
	return "k"
}

func main() {
	mm := map[string]map[string]int{"x": map[string]int{}}
	var calls map[string]int = map[string]int{}
	mm[key(calls)][valuekey(calls)] = 5
	fmt.Println(calls["outer"], calls["inner"], mm["x"]["k"])
}
