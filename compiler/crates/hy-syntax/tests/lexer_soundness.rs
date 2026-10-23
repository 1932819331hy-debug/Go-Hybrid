//! Regression tests for `hy-syntax` lexer soundness (LX6 / LX7):
//! - `\u{…}` rune escapes: overflow panic/wraparound on >8 hex digits (`\u{100000041}`),
//!   empty braces `\u{}` accepted with 0 diagnostics, and leading-zero valid escapes (`\u{0000000041}`).
//! - Octal escapes `\NNN`: unbounded loop panic (`\1777777777777`), silent `None` without
//!   diagnostic (`\7777777`), greedy >3-digit consumption (`\1012`), `\0NN` 3-digit octals,
//!   and out-of-range byte values `> 255` (`\400`..`\777`).
//! - Malformed integer literals (`0x`, `0b`, `0o`, `0x1g`, `0b102`, `0o78`, `089`).
//! - Empty `''` and multi-character `'ab'` rune literals without quote desynchronization.

use hy_syntax::{Tok, lex, parse_file};

fn lex_errs(src: &str) -> Vec<hy_diag::Diagnostic> {
    match lex("test.hy", src) {
        Ok(toks) => panic!("expected lex failure for `{src}`, got tokens: {toks:?}"),
        Err(diags) => diags,
    }
}

#[test]
fn rune_u_brace_overflow_does_not_panic_or_wrap_to_ascii() {
    // Previously panicked in debug (`attempt to multiply with overflow`) and
    // wrapped modulo 2^32 to 'A' (0x41) in release builds.
    let errs = lex_errs("fn main() { c := '\\u{100000041}' }");
    assert!(
        errs.iter().any(|d| d.code == "HYB-E0002"
            && d.tier0_rule.as_deref() == Some("LX6")
            && d.message.contains("not a Unicode scalar value")),
        "expected HYB-E0002 [LX6] for overflowing \\u{{...}}, got: {errs:?}"
    );
}

#[test]
fn rune_u_brace_empty_is_rejected_fail_closed() {
    // Previously accepted `\u{}` with 0 diagnostics and produced `'\0'`.
    let errs = lex_errs("fn main() { c := '\\u{}' }");
    assert!(
        errs.iter().any(|d| d.code == "HYB-E0002"
            && d.tier0_rule.as_deref() == Some("LX6")
            && d.message.contains("expected hex digits then `}`")),
        "expected HYB-E0002 [LX6] for empty \\u{{}}, got: {errs:?}"
    );
}

#[test]
fn rune_u_brace_leading_zeros_stay_accepted() {
    let toks = lex("test.hy", "fn main() { c := '\\u{0000000041}' }").expect("valid escape");
    assert!(
        toks.iter().any(|t| t.tok == Tok::CharLit('A')),
        "expected CharLit('A'), got: {toks:?}"
    );
}

#[test]
fn octal_escape_unbounded_digits_do_not_panic_or_silently_drop() {
    // Previously `\1777777777777` panicked with `attempt to multiply with overflow`
    // and `\1012` greedily consumed 4 octal digits instead of stopping at 3 (`\101` + `'2'`).
    let toks = lex("test.hy", r#"fn main() { s := "\1012" }"#).expect("valid 3-digit octal + '2'");
    assert!(
        toks.iter().any(|t| t.tok == Tok::StrChunk("A2".into())),
        "expected StrChunk(\"A2\"), got: {toks:?}"
    );

    let toks2 =
        lex("test.hy", r#"fn main() { s := "\1777777777777" }"#).expect("valid \\177 + digits");
    assert!(
        toks2
            .iter()
            .any(|t| t.tok == Tok::StrChunk("\u{7f}7777777777".into())),
        "expected \\177 followed by literal 7s, got: {toks2:?}"
    );
}

#[test]
fn octal_escape_leading_zero_and_above_255_fail_closed() {
    let toks = lex("test.hy", r#"fn main() { s := "\0\000\012\377" }"#).expect("valid octals");
    assert!(
        toks.iter()
            .any(|t| t.tok == Tok::StrChunk("\0\0\n\u{ff}".into())),
        "expected \\0, \\000, \\012, \\377 to decode cleanly, got: {toks:?}"
    );

    for src in [
        r#"fn main() { s := "\400" }"#,
        r#"fn main() { s := "\777" }"#,
        r#"fn main() { s := "\7777777" }"#,
    ] {
        let errs = lex_errs(src);
        assert!(
            errs.iter().any(|d| d.code == "HYB-E0002"
                && d.tier0_rule.as_deref() == Some("LX7")
                && d.message.contains("invalid octal escape")),
            "expected HYB-E0002 [LX7] on `{src}`, got: {errs:?}"
        );
    }

    let rune_errs = lex_errs("fn main() { c := '\\400' }");
    assert!(
        rune_errs.iter().any(|d| d.code == "HYB-E0002"
            && d.tier0_rule.as_deref() == Some("LX6")
            && d.message.contains("invalid octal escape")),
        "expected HYB-E0002 [LX6] on `'\\400'`, got: {rune_errs:?}"
    );
}

#[test]
fn malformed_prefixed_and_octal_integer_literals_fail_closed() {
    for src in [
        "fn main() { x := 0x }",
        "fn main() { x := 0b }",
        "fn main() { x := 0o }",
        "fn main() { x := 0x_ }",
        "fn main() { x := 0x1g }",
        "fn main() { x := 0b102 }",
        "fn main() { x := 0o78 }",
        "fn main() { x := 08 }",
        "fn main() { x := 09 }",
        "fn main() { x := 089 }",
        "fn main() { x := 0_8 }",
    ] {
        let errs = lex_errs(src);
        assert!(
            errs.iter()
                .any(|d| d.code == "HYB-E0002" && d.tier0_rule.as_deref() == Some("LX6")),
            "expected HYB-E0002 [LX6] for `{src}`, got: {errs:?}"
        );
    }

    // Valid integer and float literals (including leading-zero floats like 08.5 and 08e2) stay accepted.
    for valid in [
        "fn main() { x := 0 }",
        "fn main() { x := 00 }",
        "fn main() { x := 0755 }",
        "fn main() { x := 0o755 }",
        "fn main() { x := 0xDEAD_BEEF }",
        "fn main() { x := 0b1010_0101 }",
        "fn main() { x := 08.5 }",
        "fn main() { x := 09e2 }",
    ] {
        assert!(
            parse_file("test.hy", valid).is_ok(),
            "expected valid number literal to parse cleanly: {valid}"
        );
    }
}

#[test]
fn empty_and_multichar_char_literals_fail_closed_without_quote_desync() {
    let errs = lex_errs("fn main() { a := '' b := 'x' }");
    assert_eq!(
        errs.len(),
        1,
        "empty char literal must not desynchronize subsequent quotes on the same line: {errs:?}"
    );
    assert!(
        errs[0].code == "HYB-E0002"
            && errs[0].tier0_rule.as_deref() == Some("LX6")
            && errs[0].message.contains("empty char literal"),
        "unexpected diagnostic: {:?}",
        errs[0]
    );

    let multi_errs = lex_errs("fn main() { a := 'ab' b := 'x' }");
    assert_eq!(
        multi_errs.len(),
        1,
        "multi-char rune literal must recover at closing quote on the same line: {multi_errs:?}"
    );
}

#[test]
fn prefixed_fraction_is_one_lx6_error_before_parsing() {
    for literal in [
        "0x1.8",
        "0o7.5",
        "0b1.0",
        "0X1.8",
        "0O7.5",
        "0B1.0",
        "0x1_0.8_0",
        "0x1.f",
        "0xDEAD.BEEF",
        "0x1.",
        "0o7.",
        "0b1.",
        "0x1.8p+2",
        "0x1.fp-2",
    ] {
        let src = format!("fn main() {{\n    n := {literal}\n    fmt.println(n)\n}}\n");
        let errors = parse_file("fraction.hy", &src).expect_err(literal);
        assert_eq!(errors.len(), 1, "{literal}: {errors:?}");
        let error = &errors[0];
        assert_eq!(error.code, "HYB-E0002", "{literal}: {error:?}");
        assert_eq!(
            error.tier0_rule.as_deref(),
            Some("LX6"),
            "{literal}: {error:?}"
        );
        assert!(error.message.contains("fractional"), "{literal}: {error:?}");
        assert_eq!((error.span.start.line, error.span.start.col), (2, 10));
    }
}

#[test]
fn prefixed_fraction_recovery_stops_at_arithmetic_operators() {
    let errors = lex_errs("0x1.8+0o7.5-0b1.0");
    assert_eq!(errors.len(), 3, "{errors:?}");
    let columns: Vec<_> = errors
        .iter()
        .map(|d| (d.span.start.col, d.span.end.col))
        .collect();
    assert_eq!(columns, [(1, 6), (7, 12), (13, 18)]);
}

#[test]
fn prefixed_integer_ranges_and_decimal_floats_keep_their_tokens() {
    for (literal, normalized) in [
        ("0x1_F", "0x1F"),
        ("0X1_F", "0x1F"),
        ("0o1_7", "0o17"),
        ("0O1_7", "0o17"),
        ("0b1_01", "0b101"),
        ("0B1_01", "0b101"),
    ] {
        for operator in ["..", "..=", "..."] {
            let src = format!("{literal}{operator}end");
            let tokens: Vec<_> = lex("range.hy", &src)
                .unwrap()
                .into_iter()
                .map(|t| t.tok)
                .collect();
            assert_eq!(
                tokens,
                [
                    Tok::Int(normalized.into()),
                    Tok::Punct(operator),
                    Tok::Ident("end".into()),
                    Tok::Eof
                ]
            );
        }
    }
    for literal in ["1.5", "2.", ".5", "1e9", "2.e+3", "08.5"] {
        let tokens = lex("float.hy", literal).unwrap();
        assert_eq!(tokens[0].tok, Tok::Float(literal.into()));
        assert_eq!(tokens[1].tok, Tok::Eof);
    }
}
