import "fmt"
struct bag { counts map[string]int }
fn main() {
    mm := map[string]map[string]int{}
    mm["a"]["b"] = 1
    fmt.println(len(mm))
}
