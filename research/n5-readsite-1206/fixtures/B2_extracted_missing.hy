import "fmt"
struct bag { counts map[string]int }
fn main() {
    mm := map[string]map[string]int{}
    inner := mm["x"]
    inner["k"] = 1
    fmt.println(len(mm), len(inner))
}
