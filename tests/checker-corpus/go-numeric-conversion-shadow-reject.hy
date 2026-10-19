// EXPECTED-VERDICT: REJECT BUILDABLE
// EXPECTED-DIAGNOSTIC: HYB-E1002
// Issue #728, SPEC-LANGUAGE LX2: a user function must not capture the Go
// spelling of a Hybrid numeric conversion. Before the hygiene check this
// program was accepted, built successfully, and printed 3500 instead of 3.5.
// Reject the declaration before emission; fields and methods remain legal
// (positive controls: hy-check/tests/go_primitive_shadowing.rs).

package main

fn float64(x int) int {
    return x * 1000
}

fn main() {
    n := 7
    half := f64(n) / 2
    fmt.println(half)
}
