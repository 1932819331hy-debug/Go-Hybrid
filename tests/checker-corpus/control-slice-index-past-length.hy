// ============================================================================
// control-slice-index-past-length — Checker corpus (bounty track: "grow the adversarial corpus").
//
// PURPOSE: a SLICE index past the length. This is the case most likely to be over-rejected while fixing the fixed-array form: Go rejects `a[300]` on `[2]int` AT COMPILE TIME but accepts `xs[300]` on a slice, because a slice's length is not part of its type.
//
// RULE CITATION: Row 13 (BUILDABLE) is satisfied: the emitted Go compiles. Hybrid makes no claim about run-time bounds panics — the same position GUARANTEES.md takes for send-on-closed-channel (Row 16, "No claim").
//
// ---- Why this is an ACCEPT (and must stay one) ----
// The checker's own bounds work must distinguish a fixed ARRAY from a SLICE. `xs := []int{1, 2}` followed by `xs[300]` compiles and panics at run time; rejecting it would be an over-reach into a property Hybrid does not claim. This control exists so the array fix cannot be written as a blanket index bound check.
//
// ---- Expected verdict (verified on main before this file was added) ----
// EXPECTED-VERDICT: ACCEPT
//   ACCEPT — 0 annotations, 0 managed. `hyb check` accepts it AND the emitted
//   Go compiles; both were run before this fixture was committed.
//
// ---- Annotations / managed ----
//   lifetime annotations: 0. `managed` blocks: 0.
// ============================================================================

fn main() {
    xs := []int{1, 2}
    fmt.println("{xs[300]}")   // legal Go: compiles, panics at run time
}
