import "fmt"
struct bag { counts map[string]int }
fn main() {
    var m map[string]int
    var b bag
    p := new(bag)
    m["k"] = 1
    b.counts["k"] = 2
    p.counts["k"] = 3
    fmt.println(m["k"], b.counts["k"], p.counts["k"])
}
