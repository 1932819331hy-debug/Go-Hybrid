import "fmt"
struct bag { counts map[string]int }
fn main() {
    bags := map[string]bag{}
    b, ok := bags["x"]
    if !ok { b.counts["k"] = 1 }
    fmt.println(ok, len(bags), len(b.counts))
}
