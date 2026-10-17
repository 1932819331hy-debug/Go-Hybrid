//! multi_value_alias_freeze.rs — regression tests for multi-value define and
//! assignment alias / may-share tracking (P0 soundness).
//!
//! Multi-value defines (`m2, n := split_map(m)`) and multi-value assignments
//! (`m2, n = split_map(m)`) previously omitted `record_may_share`,
//! `record_alias`, and `record_closure_mutations`. Consequently, when a function
//! returned a reference-bearing value along with a secondary value (e.g.
//! `(map[string]int, int)`), moving the original binding `m` into a spawned task
//! left the peer binding `m2` completely unfrozen. A subsequent write to `m2` in
//! the parent task produced concurrent map writes under the Go runtime while
//! `hyb check` accepted the program with 0 diagnostics.
//!
//! The fix wires target-shape aware `record_may_share`, `record_alias`, and
//! `record_closure_mutations` into both multi-name defines and multi-name
//! assignments, ensuring reference peers freeze on move while scalar copy-outs
//! stay accepted.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let path = "inline.hy";
    let ast = parse_file(path, src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

const MULTI_VALUE_CALL_RETURN: &str = "\
fn split_map(m map[string]int) (map[string]int, int) {
    return m, len(m)
}

fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 {
        m[\"k\"] = i
    }
    done <- true
}

fn main() {
    m := map[string]int{\"k\": 0}
    m2, n := split_map(m)
    done := make(chan bool, 1)
    task worker(move m, done)
    for i in 0..10000 {
        m2[\"k\"] = i + n
    }
    <-done
}
";

#[test]
fn multi_value_call_return_alias_is_frozen_e3003() {
    let r = check(MULTI_VALUE_CALL_RETURN);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through multi-value returned alias, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_VALUE_CALL_SECOND_RETURN: &str = "\
fn split_map_rev(m map[string]int) (int, map[string]int) {
    return len(m), m
}

fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 {
        m[\"k\"] = i
    }
    done <- true
}

fn main() {
    m := map[string]int{\"k\": 0}
    n, m2 := split_map_rev(m)
    done := make(chan bool, 1)
    task worker(move m, done)
    for i in 0..10000 {
        m2[\"k\"] = i + n
    }
    <-done
}
";

#[test]
fn multi_value_call_second_return_alias_is_frozen_e3003() {
    let r = check(MULTI_VALUE_CALL_SECOND_RETURN);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through 2nd-position multi-value alias, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_VALUE_ASSIGN_REBIND: &str = "\
fn split_map(m map[string]int) (map[string]int, int) {
    return m, len(m)
}

fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 {
        m[\"k\"] = i
    }
    done <- true
}

fn main() {
    m := map[string]int{\"k\": 0}
    m2 := map[string]int{}
    n := 0
    m2, n = split_map(m)
    done := make(chan bool, 1)
    task worker(move m, done)
    m2[\"k\"] = n
    <-done
}
";

#[test]
fn multi_value_assign_rebind_is_frozen_e3003() {
    let r = check(MULTI_VALUE_ASSIGN_REBIND);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through multi-value rebind alias, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_VALUE_MAP_LOOKUP_NESTED_REF: &str = "\
fn worker(m map[string]map[string]int, done chan<- bool) {
    m[\"outer\"][\"inner\"] = 1
    done <- true
}

fn main() {
    m := map[string]map[string]int{\"outer\": map[string]int{\"inner\": 0}}
    sub, ok := m[\"outer\"]
    done := make(chan bool, 1)
    task worker(move m, done)
    if ok {
        sub[\"inner\"] = 2
    }
    <-done
}
";

#[test]
fn multi_value_map_lookup_nested_ref_is_frozen_e3003() {
    let r = check(MULTI_VALUE_MAP_LOOKUP_NESTED_REF);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through nested map lookup after move, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_VALUE_SCALAR_POSITIVE_CONTROL: &str = "\
fn split_map(m map[string]int) (map[string]int, int) {
    return m, len(m)
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    m := map[string]int{\"k\": 0}
    m2, n := split_map(m)
    _ = m2
    done := make(chan bool, 1)
    task worker(move m, done)
    val := n + 42
    _ = val
    <-done
}
";

#[test]
fn multi_value_scalar_return_stays_unfrozen() {
    let r = check(MULTI_VALUE_SCALAR_POSITIVE_CONTROL);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "scalar copy-out `n` must remain unfrozen after moving original map: {:?}",
        r.diagnostics
    );
}

const MULTI_VALUE_MAP_PRIMITIVE_POSITIVE_CONTROL: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    m := map[string]int{\"k\": 10}
    v, ok := m[\"k\"]
    done := make(chan bool, 1)
    task worker(move m, done)
    if ok {
        res := v + 1
        _ = res
    }
    <-done
}
";

#[test]
fn multi_value_map_lookup_primitive_stays_unfrozen() {
    let r = check(MULTI_VALUE_MAP_PRIMITIVE_POSITIVE_CONTROL);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "primitive map lookup value `v` and `ok` must remain unfrozen after moving map: {:?}",
        r.diagnostics
    );
}

const PARALLEL_ASSIGN_SWAP_POSITIVE_CONTROL: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    b := map[string]int{\"b\": 2}
    a, b = b, a
    done := make(chan bool, 1)
    task worker(move b, done)
    a[\"a\"] = 10
    <-done
}
";

#[test]
fn multi_target_swap_does_not_falsely_freeze_unmoved_operand() {
    let r = check(PARALLEL_ASSIGN_SWAP_POSITIVE_CONTROL);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "unmoved operand `a` holding swapped map must remain unfrozen after moving `b`: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_PROVENANCE: &str = "\
fn pair(a map[string]int, b map[string]int) (map[string]int, map[string]int) {
    return a, b
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    b := map[string]int{\"b\": 2}
    x, y := pair(a, b)
    _ = y
    done := make(chan bool, 1)
    task worker(move b, done)
    x[\"a\"] = 10
    <-done
}
";

#[test]
fn multi_return_call_first_slot_not_frozen_when_second_source_moved() {
    let r = check(MULTI_RETURN_PROVENANCE);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "`x` which corresponds to `a` must not be frozen when unrelated source `b` is moved: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_PROVENANCE_SECOND_SLOT_FROZEN: &str = "\
fn pair(a map[string]int, b map[string]int) (map[string]int, map[string]int) {
    return a, b
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    b := map[string]int{\"b\": 2}
    x, y := pair(a, b)
    done := make(chan bool, 1)
    task worker(move b, done)
    y[\"b\"] = 20
    _ = x
    <-done
}
";

#[test]
fn multi_return_call_second_slot_frozen_when_second_source_moved() {
    let r = check(MULTI_RETURN_PROVENANCE_SECOND_SLOT_FROZEN);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `y` which aliases moved `b`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_RETURN_PAIR_REV: &str = "\
fn pair_rev(a map[string]int, b map[string]int) (map[string]int, map[string]int) {
    return b, a
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    b := map[string]int{\"b\": 2}
    x, y := pair_rev(a, b)
    _ = x
    done := make(chan bool, 1)
    task worker(move b, done)
    y[\"a\"] = 10
    <-done
}
";

#[test]
fn multi_return_pair_rev_first_slot_unfrozen_when_second_source_moved() {
    let r = check(MULTI_RETURN_PAIR_REV);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "in `pair_rev(a, b) -> (b, a)`, moving `b` leaves `y` (aliasing `a`) unfrozen: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_SAME_TWICE: &str = "\
fn same_twice(a map[string]int) (map[string]int, map[string]int) {
    return a, a
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    x, y := same_twice(a)
    _ = x
    done := make(chan bool, 1)
    task worker(move a, done)
    y[\"k\"] = 10
    <-done
}
";

#[test]
fn multi_return_same_twice_both_slots_frozen_when_source_moved() {
    let r = check(MULTI_RETURN_SAME_TWICE);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `y` in `same_twice`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_RETURN_FRESH_AND_ALIAS: &str = "\
fn fresh_and_alias(a map[string]int) (map[string]int, map[string]int) {
    fresh := map[string]int{\"f\": 0}
    return fresh, a
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    x, y := fresh_and_alias(a)
    _ = y
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"f\"] = 10
    <-done
}
";

#[test]
fn multi_return_fresh_and_alias_fresh_slot_unfrozen_when_param_moved() {
    let r = check(MULTI_RETURN_FRESH_AND_ALIAS);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "fresh allocation in return slot 0 must not freeze when `a` is moved: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_DIRECT_LITERAL_FRESH_AND_ALIAS: &str = "\
fn fresh_and_alias(a map[string]int) (map[string]int, map[string]int) {
    return map[string]int{\"f\": 0}, a
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"a\": 1}
    x, y := fresh_and_alias(a)
    _ = y
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"f\"] = 10
    <-done
}
";

#[test]
fn multi_return_direct_literal_fresh_and_alias_accepts() {
    let r = check(MULTI_RETURN_DIRECT_LITERAL_FRESH_AND_ALIAS);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "direct literal return in slot 0 must not falsely inherit caller param roots from fallback: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_LOCAL_ALIAS_RELAY: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := a
    return x, 0
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn multi_return_local_alias_relay_preserves_provenance_rejects_e3003() {
    let r = check(MULTI_RETURN_LOCAL_ALIAS_RELAY);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` which aliases moved `a` via callee local `x := a`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_RETURN_LOCAL_ALIAS_CHAIN: &str = "\
fn relay_chain(a map[string]int) (map[string]int, int) {
    x := a
    y := x
    return y, 0
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay_chain(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn multi_return_local_alias_chain_preserves_provenance_rejects_e3003() {
    let r = check(MULTI_RETURN_LOCAL_ALIAS_CHAIN);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` which aliases moved `a` via chain `x := a; y := x; return y`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const MULTI_RETURN_LOCAL_ALIAS_REBIND_FRESH: &str = "\
fn rebind_fresh(a map[string]int) (map[string]int, int) {
    x := a
    x = map[string]int{\"fresh\": 1}
    return x, 0
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"k\": 0}
    x, n := rebind_fresh(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"fresh\"] = 2
    <-done
}
";

#[test]
fn multi_return_local_alias_rebind_fresh_accepts() {
    let r = check(MULTI_RETURN_LOCAL_ALIAS_REBIND_FRESH);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "strong update `x = fresh` must disconnect `x` from `a`: {:?}",
        r.diagnostics
    );
}

const MULTI_RETURN_BRANCH_UNION: &str = "\
fn maybe_alias(a map[string]int, flag bool) (map[string]int, int) {
    if flag {
        x := a
        return x, 0
    }
    return map[string]int{\"fresh\": 1}, 0
}

fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"k\": 0}
    x, n := maybe_alias(a, true)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn multi_return_branch_union_preserves_provenance_rejects_e3003() {
    let r = check(MULTI_RETURN_BRANCH_UNION);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` when branch may return alias of `a`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const LEXICAL_DUPLICATE_LEFT_STORE_ORDER_ACCEPTS_FIRST_MOVED: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"init\": 0}
    x := map[string]int{\"x\": 1}
    y := map[string]int{\"y\": 2}
    a, a = x, y
    done := make(chan bool, 1)
    task worker(move x, done)
    a[\"y\"] = 10
    <-done
}
";

#[test]
fn lexical_duplicate_left_store_order_accepts_first_moved() {
    let r = check(LEXICAL_DUPLICATE_LEFT_STORE_ORDER_ACCEPTS_FIRST_MOVED);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "in `a, a = x, y`, `a` ultimately points to `y`; moving `x` must leave `a` accepted: {:?}",
        r.diagnostics
    );
}

const LEXICAL_DUPLICATE_LEFT_STORE_ORDER_REJECTS_SECOND_MOVED: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}

fn main() {
    a := map[string]int{\"init\": 0}
    x := map[string]int{\"x\": 1}
    y := map[string]int{\"y\": 2}
    a, a = x, y
    done := make(chan bool, 1)
    task worker(move y, done)
    a[\"y\"] = 10
    <-done
}
";

#[test]
fn lexical_duplicate_left_store_order_rejects_second_moved() {
    let r = check(LEXICAL_DUPLICATE_LEFT_STORE_ORDER_REJECTS_SECOND_MOVED);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 because `a` points to `y` which was moved, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

// =========================================================================
// Independent Acceptance Probes (16-Probe Matrix)
// =========================================================================

const PROBE_PARALLEL_DUPLICATE_SOURCE: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 1}
    a, b = a, a
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { b[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_parallel_duplicate_source_rejects_e3003() {
    let r = check(PROBE_PARALLEL_DUPLICATE_SOURCE);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `b` which aliases `a` via `a, b = a, a`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const PROBE_PARAM_REBIND: &str = "\
fn relay(a map[string]int, b map[string]int) (map[string]int, int) {
    a = b
    return a, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a, b)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move b, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_param_rebind_rejects_e3003() {
    let r = check(PROBE_PARAM_REBIND);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` which aliases `b` via `a = b; return a`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const PROBE_LOOP_TWO_ITERATIONS: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := map[string]int{\"fresh\": 1}
    y := map[string]int{\"fresh\": 2}
    for i in 0..2 {
        _ = i
        x = y
        y = a
    }
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_loop_two_iterations_rejects_e3003() {
    let r = check(PROBE_LOOP_TWO_ITERATIONS);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` which aliases `a` after 2 loop iterations, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const PROBE_PARALLEL_LOCAL_UNSAFE: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := a
    x, y := map[string]int{\"fresh\": 1}, x
    _ = x
    return y, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_parallel_local_unsafe_rejects_e3003() {
    let r = check(PROBE_PARALLEL_LOCAL_UNSAFE);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` returning `y` from `x, y := fresh, x`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const PROBE_CLOSURE_CAPTURE: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    get := fn() map[string]int { return a }
    x := get()
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_closure_capture_rejects_e3003() {
    let r = check(PROBE_CLOSURE_CAPTURE);
    let cs = codes(&r);
    assert!(
        cs.iter().any(|c| c == "HYB-E3003"),
        "expected HYB-E3003 for write through `x` returned from closure capturing `a`, got {:?}",
        cs
    );
    assert_eq!(r.verdict, Verdict::Reject);
}

const PROBE_BRANCH_BOTH_FRESH: &str = "\
fn relay(a map[string]int, flag bool) (map[string]int, int) {
    x := a
    if flag {
        x = map[string]int{\"fresh\": 1}
    } else {
        x = map[string]int{\"fresh\": 2}
    }
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a, true)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_branch_both_fresh_accepts() {
    let r = check(PROBE_BRANCH_BOTH_FRESH);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "both branches rebind `x` to fresh literals; `x` must not retain parameter root `a`: {:?}",
        r.diagnostics
    );
}

const PROBE_BLOCK_SHADOW_FRESH: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := map[string]int{\"fresh\": 1}
    {
        x := a
        _ = x
    }
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_block_shadow_fresh_accepts() {
    let r = check(PROBE_BLOCK_SHADOW_FRESH);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "inner block shadows `x := a`; outer `x` remains fresh literal: {:?}",
        r.diagnostics
    );
}

const PROBE_BLOCK_REBIND_FRESH: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := (a)
    {
        x = map[string]int{\"fresh\": 1}
    }
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_block_rebind_fresh_accepts() {
    let r = check(PROBE_BLOCK_REBIND_FRESH);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "unconditional inner block strongly updates `x = fresh`: {:?}",
        r.diagnostics
    );
}

const PROBE_PARALLEL_LOCAL_SAFE: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x := map[string]int{\"fresh\": 1}
    x, y := a, x
    _ = x
    return y, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_parallel_local_safe_accepts() {
    let r = check(PROBE_PARALLEL_LOCAL_SAFE);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "`x, y := a, x` evaluates in pre-state; `y` gets old fresh `x`: {:?}",
        r.diagnostics
    );
}

const PROBE_LOCAL_MULTIRETURN_SLOT: &str = "\
fn pair(a map[string]int, b map[string]int) (map[string]int, map[string]int) {
    return a, b
}
fn relay(a map[string]int, b map[string]int) (map[string]int, int) {
    x, y := pair(a, b)
    _ = y
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a, b)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move b, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_local_multireturn_slot_accepts() {
    let r = check(PROBE_LOCAL_MULTIRETURN_SLOT);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "`x, y := pair(a, b)` maintains slot precision; moving `b` leaves `x` unfrozen: {:?}",
        r.diagnostics
    );
}

const PROBE_LOCAL_FRESH_CALL: &str = "\
fn fresh(a map[string]int) (map[string]int, int) {
    return map[string]int{\"fresh\": 1}, 0
}
fn relay(a map[string]int) (map[string]int, int) {
    x, n := fresh(a)
    _ = n
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    for i in 0..10000 { m[\"k\"] = i }
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    _ = b
    done := make(chan bool, 1)
    task worker(move a, done)
    for i in 0..10000 { x[\"k\"] = i }
    <-done
}
";

#[test]
fn probe_local_fresh_call_accepts() {
    let r = check(PROBE_LOCAL_FRESH_CALL);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "`x, n := fresh(a)` has known empty return slot 0; `x` must not inherit `a`: {:?}",
        r.diagnostics
    );
}

const PROBE_CONDITIONAL_PARAM_FRESH: &str = "\
fn relay(a map[string]int, flag bool) (map[string]int, int) {
    if flag { a = map[string]int{\"fresh\": 1} }
    return a, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a, false)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_conditional_param_fresh_rejects_e3003() {
    let r = check(PROBE_CONDITIONAL_PARAM_FRESH);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "conditional_param_fresh must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_ZERO_LOOP_PARAM: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    for i in 0..0 { _ = i
        a = map[string]int{\"fresh\": 1}
    }
    return a, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_zero_loop_param_rejects_e3003() {
    let r = check(PROBE_ZERO_LOOP_PARAM);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "zero_loop_param must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_IF_INIT_PARAM_SHADOW: &str = "\
fn relay(a map[string]int, flag bool) (map[string]int, int) {
    if a := map[string]int{\"fresh\": 1}; flag { _ = a }
    return a, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a, true)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_if_init_param_shadow_rejects_e3003() {
    let r = check(PROBE_IF_INIT_PARAM_SHADOW);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "if_init_param_shadow must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_LOCAL_CALLABLE_SHADOW: &str = "\
fn pick(a map[string]int) (map[string]int, int) {
    return map[string]int{\"fresh\": 1}, 0
}
fn relay(a map[string]int) (map[string]int, int) {
    pick := fn(m map[string]int) (map[string]int, int) { return m, 0 }
    x, n := pick(a)
    _ = n
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_local_callable_shadow_rejects_e3003() {
    let r = check(PROBE_LOCAL_CALLABLE_SHADOW);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "local_callable_shadow must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_LOOP_FIVE_STEPS: &str = "\
fn relay(a map[string]int) (map[string]int, int) {
    x0 := map[string]int{\"fresh\": 0}
    x1 := map[string]int{\"fresh\": 1}
    x2 := map[string]int{\"fresh\": 2}
    x3 := map[string]int{\"fresh\": 3}
    x4 := map[string]int{\"fresh\": 4}
    for i in 0..5 {
        _ = i
        x0 = x1
        x1 = x2
        x2 = x3
        x3 = x4
        x4 = a
    }
    return x0, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_loop_five_steps_rejects_e3003() {
    let r = check(PROBE_LOOP_FIVE_STEPS);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "loop_five_steps must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_LOOP_BREAK_EXIT: &str = "\
fn relay(a map[string]int, flag bool) (map[string]int, int) {
    x := map[string]int{\"fresh\": 1}
    for i in 0..1 {
        _ = i
        x = a
        if flag { break }
        x = map[string]int{\"fresh\": 2}
    }
    return x, 0
}
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    x, n := relay(a, true)
    _ = n
    done := make(chan bool, 1)
    task worker(move a, done)
    x[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_loop_break_exit_rejects_e3003() {
    let r = check(PROBE_LOOP_BREAK_EXIT);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        codes(&r).contains(&"HYB-E3003".to_string()),
        "loop_break_exit must reject with HYB-E3003: {:?}",
        r.diagnostics
    );
}

const PROBE_FRESH_CALLS_INDEPENDENT: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn fresh() map[string]int {
    return map[string]int{\"k\": 0}
}
fn main() {
    a := map[string]int{\"k\": 0}
    b := map[string]int{\"k\": 1}
    a, b = fresh(), fresh()
    done := make(chan bool, 1)
    task worker(move a, done)
    b[\"k\"] = 2
    <-done
}
";

#[test]
fn probe_fresh_calls_independent_accepts() {
    let r = check(PROBE_FRESH_CALLS_INDEPENDENT);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "two distinct fresh() calls must not share storage: {:?}",
        r.diagnostics
    );
}

const PROBE_SCALAR_COPY_ASSIGNMENT: &str = "\
fn worker(m map[string]int, done chan<- bool) {
    m[\"k\"] = 1
    done <- true
}
fn main() {
    a := map[string]int{\"k\": 0}
    n := 0
    x := map[string]int{\"k\": 1}
    n, x = len(a), a
    done := make(chan bool, 1)
    task worker(move x, done)
    _ = n + 1
    <-done
}
";

#[test]
fn probe_scalar_copy_assignment_accepts() {
    let r = check(PROBE_SCALAR_COPY_ASSIGNMENT);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "scalar copy target n must not be linked to x: {:?}",
        r.diagnostics
    );
}
