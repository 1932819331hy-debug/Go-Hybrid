import "fmt"
struct bag { counts map[string]int }
fn main() {
    mm := map[string]map[string]int{}
    m, ok := mm["x"]
    fmt.println(ok, len(mm), len(m))
}
