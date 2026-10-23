import "fmt"
struct bag { counts map[string]int }
fn main() {
    ch := make(chan map[string]int, 1)
    close(ch)
    m, ok := <-ch
    if !ok { m["k"] = 1 }
    fmt.println(ok, len(m))
}
