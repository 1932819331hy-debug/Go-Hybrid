//! Diagnostic-location regressions for malformed assignment targets.

use hy_diag::Pos;
use hy_syntax::parse_file;

fn assert_invalid_target_location(src: &str) {
    let diagnostics = parse_file("invalid-target.hy", src).expect_err("invalid target must reject");
    let diagnostic = diagnostics
        .iter()
        .find(|d| d.code == "HYB-E0002" && d.message.contains("`1` is not assignable"))
        .expect("expected the invalid-target diagnostic");
    assert_eq!(
        diagnostic.span.start,
        Pos::new(2, 5),
        "the diagnostic must point to the invalid target `1`, not a later token: {diagnostic:?}"
    );
}

#[test]
fn invalid_increment_target_points_to_target_not_following_statement() {
    assert_invalid_target_location("fn main() {\n    1++\n    return\n}\n");
}

#[test]
fn invalid_assignment_target_points_to_target_not_rhs() {
    assert_invalid_target_location("fn main() {\n    1 =\n        2\n}\n");
}

#[test]
fn valid_identifier_targets_still_parse() {
    let src = "fn main() {\n    x := 1\n    x++\n    x =\n        2\n    return\n}\n";
    assert!(parse_file("valid-target.hy", src).is_ok());
}
