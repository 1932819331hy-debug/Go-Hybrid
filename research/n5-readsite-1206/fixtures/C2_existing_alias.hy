import "fmt"
struct bag { counts map[string]int }
fn main() {
    mm := map[string]map[string]int{"x": map[string]int{"k": 1}}
    a := mm["x"]
    b, ok := mm["x"]
    a["k"] = 2
    fmt.println(mm["x"]["k"], b["k"], ok, len(mm))
}
