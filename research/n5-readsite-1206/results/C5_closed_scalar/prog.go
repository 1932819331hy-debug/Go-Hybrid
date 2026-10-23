package main

import (
	"fmt"
)

type bag struct {
	counts map[string]int
}

func main() {
	ch := make(chan int, 1)
	close(ch)
	v, ok := <-ch
	fmt.Println(v, ok)
}
