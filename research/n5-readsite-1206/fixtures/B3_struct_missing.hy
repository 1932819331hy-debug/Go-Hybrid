import "fmt"
struct bag { counts map[string]int }
fn main() {
    gs := map[string]bag{}
    g := gs["x"]
    g.counts["k"] = 10
    fmt.println(len(gs), len(g.counts))
}
