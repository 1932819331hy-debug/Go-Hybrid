import "fmt"
struct bag { counts map[string]int }
fn key(c map[string]int) string {
    c["outer"] += 1
    return "x"
}
fn valuekey(c map[string]int) string {
    c["inner"] += 1
    return "k"
}
fn main() {
    mm := map[string]map[string]int{"x": map[string]int{}}
    var calls map[string]int
    mm[key(calls)][valuekey(calls)] = 5
    fmt.println(calls["outer"], calls["inner"], mm["x"]["k"])
}
