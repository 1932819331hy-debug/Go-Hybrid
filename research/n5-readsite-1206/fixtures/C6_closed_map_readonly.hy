import "fmt"
struct bag { counts map[string]int }
fn main() {
    ch := make(chan map[string]int, 1)
    close(ch)
    a, oka := <-ch
    b, okb := <-ch
    fmt.println(oka, okb, len(a), len(b))
}
