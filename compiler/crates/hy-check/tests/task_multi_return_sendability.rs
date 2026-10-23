//! Every forwarded result must satisfy the task boundary's sendability proof.
use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("task_multi_return.hy", src)
        .unwrap_or_else(|diags| panic!("must parse: {diags:?}\n{src}"));
    check_file(&ast)
}

fn errors(r: &CheckResult) -> Vec<&str> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.as_str())
        .collect()
}

fn rejected(src: &str, code: &str) {
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{src}\n{:?}", r.diagnostics);
    assert!(
        errors(&r).contains(&code),
        "expected {code}: {src}\n{:?}",
        r.diagnostics
    );
}

fn accepted(src: &str) {
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "{src}\n{:?}", r.diagnostics);
    assert!(errors(&r).is_empty());
}

#[test]
fn later_mutable_results_are_rejected() {
    for ty in ["map[string]int", "[]int", "*int"] {
        for (results, values, params) in [
            (format!("int, {ty}"), "1, v", format!("n int, v {ty}")),
            (
                format!("int, bool, {ty}"),
                "1, true, v",
                format!("n int, b bool, v {ty}"),
            ),
        ] {
            let src = format!("fn pair(v {ty}) ({results}) {{ return {values} }}\nfn worker({params}) {{}}\nfn launch(v {ty}) {{ task worker(pair(v)) }}\n");
            rejected(&src, "HYB-E3001");
        }
    }
}

#[test]
fn later_unknown_results_fail_closed() {
    for (results, values, params) in [
        ("int, any", "1, v", "n int, v any"),
        ("int, bool, any", "1, true, v", "n int, b bool, v any"),
    ] {
        rejected(&format!("fn pair(v any) ({results}) {{ return {values} }}\nfn worker({params}) {{}}\nfn launch(v any) {{ task worker(pair(v)) }}\n"), "HYB-E3005");
    }
}

#[test]
fn all_sendable_pairs_and_triples_remain_legal() {
    for (results, values, params) in [
        ("int, bool", "1, true", "n int, b bool"),
        (
            "int, bool, string",
            "1, true, \"ok\"",
            "n int, b bool, s string",
        ),
    ] {
        accepted(&format!("fn pair() ({results}) {{ return {values} }}\nfn worker({params}) {{}}\nfn main() {{ task worker(pair()) }}\n"));
    }
    accepted("fn pair(ch chan int) (int, chan int) { return 1, ch }\nfn worker(n int, ch chan int) {}\nfn launch(ch chan int) { task worker(pair(ch)) }\n");
}

#[test]
fn parenthesized_calls_and_declared_methods_check_later_results() {
    for call in ["pair(v)", "(pair(v))", "((pair)(v))", "((pair(v)))"] {
        rejected(&format!("fn pair(v []int) (int, []int) {{ return 1, v }}\nfn worker(n int, v []int) {{}}\nfn launch(v []int) {{ task worker({call}) }}\n"), "HYB-E3001");
    }
    rejected("struct pair_box { n int }\nfn (b pair_box) pair(v []int) (int, []int) { return b.n, v }\nfn worker(n int, v []int) {}\nfn launch(v []int) { b := pair_box{n: 1}\ntask worker(b.pair(v)) }\n", "HYB-E3001");
    accepted("struct pair_box { n int }\nfn (b pair_box) pair() (int, bool) { return b.n, true }\nfn worker(n int, b bool) {}\nfn main() { b := pair_box{n: 1}\ntask worker(b.pair()) }\n");
}

#[test]
fn try_strips_only_the_trailing_error() {
    for call in ["pair(v, err)?", "(pair(v, err)?)", "(pair(v, err))?"] {
        rejected(&format!("fn pair(v []int, err error) (int, []int, error) {{ return 1, v, err }}\nfn worker(n int, v []int) {{}}\nfn launch(v []int, err error) error {{ task worker({call})\nreturn err }}\n"), "HYB-E3001");
    }
    let r = check("fn pair(err error) (int, bool, error) { return 1, true, err }\nfn worker(n int, b bool) {}\nfn launch(err error) error { task worker(pair(err)?)\nreturn err }\n");
    // Main already rejects try-forwarding in its arity pass. Keep that
    // separate behavior while proving the removed error is not classified.
    assert_eq!(errors(&r), vec!["HYB-E1002"], "{:?}", r.diagnostics);
    rejected("fn pair(v any, err error) (int, any, error) { return 1, v, err }\nfn worker(n int, v any) {}\nfn launch(v any, err error) error { task worker(pair(v, err)?)\nreturn err }\n", "HYB-E3005");
    rejected("fn pair(err error) (int, error, error) { return 1, err, err }\nfn worker(n int, err error) {}\nfn launch(err error) error { task worker(pair(err)?)\nreturn err }\n", "HYB-E3005");
}

#[test]
fn indirect_field_and_index_calls_already_fail_closed() {
    rejected("struct holder { f fn(v map[string]int) int map[string]int }\nfn pair(v map[string]int) (int, map[string]int) { return 1, v }\nfn worker(n int, v map[string]int) {}\nfn launch(v map[string]int) { h := holder{f: pair}\ntask worker(h.f(v)) }\n", "HYB-E3005");
    rejected("fn pair(v map[string]int) (int, map[string]int) { return 1, v }\nfn worker(n int, v map[string]int) {}\nfn launch(v map[string]int) { fs := []fn(v map[string]int) int map[string]int{pair}\ntask worker(fs[0](v)) }\n", "HYB-E3005");
}

#[test]
fn single_result_and_channel_boundaries_keep_their_rules() {
    accepted("fn one() int { return 1 }\nfn worker(n int) {}\nfn main() { task worker(one()) }\n");
    rejected("fn one(v []int) []int { return v }\nfn worker(v []int) {}\nfn launch(v []int) { task worker(one(v)) }\n", "HYB-E3001");
    accepted("fn one() int { return 1 }\nfn launch(ch chan int) { ch <- one() }\n");
    rejected(
        "fn one(v []int) []int { return v }\nfn launch(ch chan []int, v []int) { ch <- one(v) }\n",
        "HYB-E3002",
    );
}

#[test]
fn managed_forwarding_is_audited() {
    let r = check("fn pair(v []int) (int, []int) { return 1, v }\nfn worker(n int, v []int) {}\nfn launch(v []int) { managed(\"external synchronization\", \"mutex\") { task worker(pair(v)) } }\n");
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
    assert!(
        r.diagnostics.iter().any(|d| d.code == "HYB-E8001"),
        "{:?}",
        r.diagnostics
    );
    assert_eq!(r.managed.managed_blocks, 1);
    assert_eq!(r.managed.sites_via_managed, 1);
}

#[test]
fn closure_and_handle_results_are_not_transferred_implicitly() {
    rejected("type callback = fn()\nconst anchor = 0\nfn pair(v callback) (int, callback) { return anchor, v }\nfn worker(n int, v callback) {}\nfn launch(v callback) { task worker(pair(v)) }\n", "HYB-E3001");
    for ty in ["serde_json.value", "serde_json.view"] {
        rejected(&format!("import \"rust/serde_json\"\nfn pair(v {ty}) (int, {ty}) {{ return 1, v }}\nfn worker(n int, v {ty}) {{}}\nfn launch(v {ty}) {{ task worker(pair(v)) }}\n"), "HYB-E3001");
    }
}

#[test]
fn function_bindings_and_inline_calls_check_every_result() {
    rejected("fn pair(v []int) (int, []int) { return 1, v }\nfn worker(n int, v []int) {}\nfn launch(v []int) { f := pair\ntask worker(f(v)) }\n", "HYB-E3001");
    rejected("fn worker(n int, v []int) {}\nfn launch(v []int) { task worker((fn(a []int) (int, []int) { return 1, a })(v)) }\n", "HYB-E3001");
    accepted("fn pair() (int, bool) { return 1, true }\nfn worker(n int, b bool) {}\nfn main() { f := pair\ntask worker(f()) }\n");
}

#[test]
fn try_without_an_error_does_not_hide_the_last_value() {
    // Malformed try expressions must not gain a sendability proof by
    // blindly dropping an arbitrary final result.
    rejected("fn pair(v []int) (int, []int) { return 1, v }\nfn worker(n int, v []int) {}\nfn launch(v []int) { task worker(pair(v)?) }\n", "HYB-E3001");
}

#[test]
fn multi_result_channel_send_still_rejects_its_arity() {
    rejected(
        "fn pair() (int, int) { return 1, 2 }\nfn launch(ch chan int) { ch <- pair() }\n",
        "HYB-E2002",
    );
}

#[test]
fn managed_safe_results_do_not_add_a_bypass() {
    let r = check("fn pair() (int, bool) { return 1, true }\nfn worker(n int, b bool) {}\nfn main() { managed(\"worker lifecycle\", \"actor\") { task worker(pair()) } }\n");
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
    assert_eq!(r.managed.sites_via_managed, 0);
    assert!(!r.diagnostics.iter().any(|d| d.code == "HYB-E8001"));
}

#[test]
fn generic_binders_cannot_borrow_sendability_from_primitive_or_package_names() {
    for (prefix, binder) in [("", "int"), ("type t = int\n", "t")] {
        for call in ["pair[[]int](v)", "(pair[[]int])(v)", "(pair[[]int](v))"] {
            rejected(&format!("{prefix}fn pair[{binder} any](v {binder}) (bool, {binder}) {{ return true, v }}\nfn worker(b bool, v []int) {{ v[0] = 1 }}\nfn main() {{ v := []int{{0}}\ntask worker({call})\nv[0] = 2 }}\n"), "HYB-E3005");
        }
    }
}

#[test]
fn generic_concrete_results_and_channel_results_stay_sendable() {
    accepted("type t = int\nfn pair[t any](v t) (int, bool) { return 1, true }\nfn worker(n int, b bool) {}\nfn main() { task worker(pair[[]int]([]int{0})) }\n");
    accepted("type t = int\nfn pair[t any](ch chan t) (bool, chan t) { return true, ch }\nfn worker(b bool, ch chan []int) {}\nfn launch(ch chan []int) { task worker(pair[[]int](ch)) }\n");
    accepted("type count = int\nfn pair() (bool, count) { return true, 1 }\nfn worker(b bool, v count) {}\nfn main() { task worker(pair()) }\n");
}

#[test]
fn generic_binder_unknown_remains_an_audited_managed_escape() {
    let r = check("type t = int\nfn pair[t any](v t) (bool, t) { return true, v }\nfn worker(b bool, v []int) {}\nfn main() { v := []int{0}\nmanaged(\"external synchronization\", \"mutex\") { task worker(pair[[]int](v)) } }\n");
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E8001"));
    assert_eq!(r.managed.sites_via_managed, 1);
}

#[test]
fn generic_context_survives_function_bindings_and_inline_result_types() {
    rejected("fn pair[int any](v int) (bool, int) { return true, v }\nfn worker(b bool, v []int) {}\nfn main() { v := []int{0}\nf := pair[[]int]\ntask worker(f(v)) }\n", "HYB-E3005");
    rejected("type t = int\nfn worker(b bool, v any) {}\nfn launch[t any](v t) { task worker((fn(v t) (bool, t) { return true, v })(v)) }\n", "HYB-E3005");
    rejected("type t = int\nfn worker(b bool, v any) {}\nfn launch[t any](f fn(v t) bool t, v t) { task worker(f(v)) }\n", "HYB-E3005");
}

#[test]
fn generic_receiver_binders_shadow_package_aliases() {
    for recv in ["box[t]", "*box[t]"] {
        rejected(&format!("type t = int\nstruct box[k any] {{ v k }}\nfn (b {recv}) pair() (bool, t) {{ return true, b.v }}\nfn worker(b bool, v []int) {{}}\nfn main() {{ b := box[[]int]{{v: []int{{0}}}}\ntask worker(b.pair()) }}\n"), "HYB-E3005");
    }
    accepted("type t = int\nstruct box[k any] { ch chan k }\nfn (b box[t]) pair() (bool, chan t) { return true, b.ch }\nfn worker(b bool, ch chan []int) {}\nfn launch(ch chan []int) { b := box[[]int]{ch: ch}\ntask worker(b.pair()) }\n");
}

#[test]
fn generic_option_and_atomic_pointer_names_do_not_inherit_package_proofs() {
    rejected("type t = int\nfn pair[t any](v option[t]) (bool, option[t]) { return true, v }\nfn worker(b bool, v option[[]int]) {}\nfn launch(v option[[]int]) { task worker(pair[[]int](v)) }\n", "HYB-E3005");
    rejected("import \"sync/atomic\"\ntype t = atomic.int64\nfn pair[t any](v *t) (bool, *t) { return true, v }\nfn worker(b bool, v *int) {}\nfn launch(v *int) { task worker(pair[int](v)) }\n", "HYB-E3005");
}

#[test]
fn generic_callers_do_not_change_package_declaration_context() {
    accepted("type t = int\nfn pair() (bool, t) { return true, 1 }\nfn worker(b bool, v t) {}\nfn launch[t any]() { task worker(pair()) }\n");
    accepted("type t = int\ntype callback = fn() bool t\nconst anchor = 0\nfn worker(b bool, v t) {}\nfn launch[t any](f callback) { task worker(f()) }\n");
    accepted("type t = int\nfn pair[t any](ch chan t) (bool, option[chan t]) { return true, some(ch) }\nfn worker(b bool, ch option[chan []int]) {}\nfn launch(ch chan []int) { task worker(pair[[]int](ch)) }\n");
}

#[test]
fn generic_error_binder_is_not_the_trailing_error_type() {
    rejected("fn pair[error any](v error) (bool, error) { return true, v }\nfn worker(b bool, v []int) {}\nfn launch(v []int) { task worker(pair[[]int](v)?) }\n", "HYB-E3005");
}
