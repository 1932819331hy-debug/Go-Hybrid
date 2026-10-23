import "fmt"
struct bag { counts map[string]int }
fn main() {
    ch := make(chan int, 1)
    close(ch)
    v, ok := <-ch
    fmt.println(v, ok)
}
