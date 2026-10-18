//! Issue #728: no source binding may hijack a lowered numeric conversion.
//! Paired controls ensure that only declarations, not ordinary conversions,
//! are rejected. Tested at the checker boundary, before hyb invokes Go.

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("issue-728.hy", src).expect("valid Hybrid source");
    check_file(&ast)
}

fn has_hygiene_error(src: &str) {
    let result = check(src);
    assert_eq!(result.verdict, Verdict::Reject, "source: {src}");
    assert!(
        result
            .diagnostics
            .iter()
            .any(|d| d.code == "HYB-E1002"
                && d.message.contains("shadow an emitted Go primitive type")),
        "expected the targeted primitive-shadow diagnostic for {src}\n{:?}",
        result.diagnostics
    );
}

#[test]
fn rejects_numeric_conversion_hijack_by_function() {
    has_hygiene_error(
        "fn float64(x int) int { return x * 1000 }\n\
         fn main() { n := 7\n half := f64(n) / 2\n fmt.println(half) }\n",
    );
}

#[test]
fn rejects_local_numeric_shadow_and_nested_block() {
    has_hygiene_error(
        "fn main() { int64 := 3\n x := i64(7)\n fmt.println(int64, x) }\n",
    );
    has_hygiene_error(
        "fn main() { if true { float32 := 2\n fmt.println(float32) } }\n",
    );
}

#[test]
fn rejects_parameter_and_closure_shadowing() {
    has_hygiene_error("fn use_it(uint8 int) int { return uint8 }\nfn main() { fmt.println(use_it(3)) }\n");
    has_hygiene_error(
        "fn main() { f := fn(float64 int) int { return float64 }\n fmt.println(f(3)) }\n",
    );
}

#[test]
fn rejects_package_type_and_for_range_bindings() {
    has_hygiene_error("type float64 = int\nfn main() { fmt.println(1) }\n");
    has_hygiene_error("fn main() { for int32 in 0..2 { fmt.println(int32) } }\n");
}

#[test]
fn legal_spec_conversions_and_other_identifiers_remain_accepted() {
    let result = check(
        "fn main() { n := 7\n\
         half := f64(n) / 2\n\
         full := i64(n)\n\
         uint8_count := u8(7)\n\
         fmt.println(half, full, uint8_count) }\n",
    );
    assert_eq!(result.verdict, Verdict::Accept, "{:?}", result.diagnostics);
}
