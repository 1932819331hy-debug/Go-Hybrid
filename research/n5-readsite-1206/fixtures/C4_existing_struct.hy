import "fmt"
struct bag { counts map[string]int }
fn main() {
    bags := map[string]bag{"x": bag{}}
    b, ok := bags["x"]
    b.counts["k"] = 4
    fmt.println(bags["x"].counts["k"], ok, len(bags))
}
