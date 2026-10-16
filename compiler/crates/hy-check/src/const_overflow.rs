//! const_overflow.rs — constant initializers that overflow a declared
//! fixed-width integer type (issue #148).
//!
//! `var n int8 = 300` (and the file-level `const` twin) pass `hyb check`
//! with 0 diagnostics today, then fail `go vet` / `go build` with
//! `cannot use 300 as int8 value (overflows)`. The accepted-should-
//! emit-buildable-Go contract requires a checker diagnostic (HYB-E6002)
//! for that narrow constant case. Non-constant operands stay out of
//! scope — Go truncates at run time via an explicit conversion
//! (SPEC-LANGUAGE X4), and the conversion form `int8(x)` is unaffected.

use crate::model::{unwrap_parens, Checker};
use hy_diag::{Diagnostic, Span};
use hy_syntax::{Expr, LitKind, TypeExpr, UnOp};

impl<'a> Checker<'a> {
    /// When `ty` is a fixed-width integer and `value` is an integer
    /// constant outside that type's range, emit HYB-E6002.
    pub(crate) fn check_const_overflow_init(&mut self, name: &str, ty: &TypeExpr, value: &Expr) {
        let TypeExpr::Named {
            name: ty_name,
            span: ty_span,
        } = ty
        else {
            return;
        };
        let Some((lo, hi)) = fixed_width_int_range(ty_name) else {
            return;
        };
        let Some(v) = const_int_value(value) else {
            return;
        };
        if v.fits_in_range(lo, hi) {
            return;
        }
        let span = value.span();
        let msg = format!(
            "constant `{}` overflows declared type `{}` of `{}` (HYB-E6002)",
            value.render(),
            ty_name,
            name
        );
        let notes = vec![
            format!(
                "why: Go rejects a constant initializer outside `{}`'s range (`cannot use … as {} value (overflows)`), so an accepted Hybrid program would emit unbuildable Go (issue #148)",
                ty_name, ty_name
            ),
            format!(
                "fix: use a constant in [{}, {}], widen the declared type, or convert from a variable if truncation is intentional (`x := {}; y := {}(x)`)",
                lo, hi, value.render(), ty_name
            ),
            format!(
                "note: the declared type was written here ({})",
                span_hint(*ty_span)
            ),
        ];
        self.diags.push(
            Diagnostic::error("HYB-E6002", self.path.clone(), span, msg).with_notes(notes),
        );
    }

    /// When a variable declaration has no explicit type (`:=`, `let`, or `var`
    /// without ascription, issue #228 Family D), Go assigns the default type
    /// `int` (64-bit on all targeted LP64/LLP64 platforms). If `value` is an integer
    /// constant outside `int`'s range, emit HYB-E6002.
    pub(crate) fn check_default_int_overflow(&mut self, name: &str, value: &Expr) {
        let (lo, hi) = target_int_range();
        let Some(v) = const_int_value(value) else {
            return;
        };
        if v.fits_in_range(lo, hi) {
            return;
        }
        let span = value.span();
        let msg = format!(
            "constant `{}` overflows default type `int` of `{}` (HYB-E6002)",
            value.render(),
            name
        );
        let notes = vec![
            "why: Go assigns default type `int` to untyped integer constants in variable declarations and rejects values outside `int`'s range (`cannot use … as int value (overflows)`), so an accepted Hybrid program would emit unbuildable Go (issue #148, #228)"
                .to_string(),
            format!(
                "fix: declare an explicit wider type (`var {} u64 = {}`), use an untyped constant (`const {} = {}`), or convert from a variable if intentional",
                name,
                value.render(),
                name,
                value.render()
            ),
        ];
        self.diags.push(
            Diagnostic::error("HYB-E6002", self.path.clone(), span, msg).with_notes(notes),
        );
    }
}

fn span_hint(span: Span) -> String {
    format!("{}:{}", span.start.line, span.start.col)
}

/// Target word size configuration for platform-dependent integer types
/// (`int`, `uint`, `uintptr`, `isize`, `usize`).
///
/// Current Hybrid compiler and runtime architecture:
/// - Hybrid's runtime (`hybridge`) and assembly trampolines (`call_amd64.s`, `call_arm64.s`,
///   `call_win64.s`) exclusively target 64-bit platforms (amd64, arm64).
/// - Go on all targeted platforms defines `int` as 64-bit (`int64`).
/// - Should 32-bit compilation targets (e.g. 32-bit ARM or x86) be introduced in the future,
///   the target word size can be parameterized here via target triple/target configuration.
pub(crate) const TARGET_INT_WIDTH_BITS: u32 = 64;

#[inline]
pub(crate) fn target_int_range() -> (i128, i128) {
    match TARGET_INT_WIDTH_BITS {
        32 => (i32::MIN as i128, i32::MAX as i128),
        64 => (i64::MIN as i128, i64::MAX as i128),
        _ => (i64::MIN as i128, i64::MAX as i128),
    }
}

#[inline]
pub(crate) fn target_uint_range() -> (i128, i128) {
    match TARGET_INT_WIDTH_BITS {
        32 => (0, u32::MAX as i128),
        64 => (0, u64::MAX as i128),
        _ => (0, u64::MAX as i128),
    }
}

/// Inclusive representable range for a Hybrid fixed-width integer type
/// name (SPEC-LANGUAGE §11 / Go's typed-constant assignment rule).
/// Platform-word `int`/`uint`/`isize`/`usize` use the 64-bit LP64 ranges
/// the emitter targets (SPEC B9 / X7).
fn fixed_width_int_range(name: &str) -> Option<(i128, i128)> {
    Some(match name {
        "i8" | "int8" => (i8::MIN as i128, i8::MAX as i128),
        "u8" | "uint8" | "byte" => (0, u8::MAX as i128),
        "i16" | "int16" => (i16::MIN as i128, i16::MAX as i128),
        "u16" | "uint16" => (0, u16::MAX as i128),
        "i32" | "int32" | "rune" => (i32::MIN as i128, i32::MAX as i128),
        "u32" | "uint32" => (0, u32::MAX as i128),
        "i64" | "int64" => (i64::MIN as i128, i64::MAX as i128),
        "int" | "isize" => target_int_range(),
        "u64" | "uint64" => (0, u64::MAX as i128),
        "uint" | "usize" | "uintptr" => target_uint_range(),
        _ => return None,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ConstInt {
    Value(i128),
    PosOverflow,
    NegOverflow,
}

impl ConstInt {
    pub(crate) fn fits_in_range(&self, lo: i128, hi: i128) -> bool {
        match *self {
            ConstInt::Value(v) => v >= lo && v <= hi,
            ConstInt::PosOverflow | ConstInt::NegOverflow => false,
        }
    }
}

/// Evaluate a pure integer constant expression the checker can see without
/// a full constant folder: int literals (any base the lexer keeps),
/// unary `-`, and parentheses. Anything else (idents, calls, binary ops)
/// is out of scope for HYB-E6002.
fn const_int_value(e: &Expr) -> Option<ConstInt> {
    match unwrap_parens(e) {
        Expr::Lit {
            kind: LitKind::Int(s),
            ..
        } => parse_int_lit(s),
        Expr::Unary {
            op: UnOp::Neg,
            operand,
            ..
        } => {
            let v = const_int_value(operand)?;
            match v {
                ConstInt::Value(v) => match v.checked_neg() {
                    Some(neg) => Some(ConstInt::Value(neg)),
                    None => Some(ConstInt::PosOverflow),
                },
                ConstInt::PosOverflow => Some(ConstInt::NegOverflow),
                ConstInt::NegOverflow => Some(ConstInt::PosOverflow),
            }
        }
        _ => None,
    }
}

fn parse_int_lit(raw: &str) -> Option<ConstInt> {
    let s: String = raw.chars().filter(|c| *c != '_').collect();
    if s.is_empty() {
        return None;
    }
    let bytes = s.as_bytes();
    let (radix, slice) = if bytes.len() >= 2 && bytes[0] == b'0' {
        match bytes[1].to_ascii_lowercase() {
            b'x' => (16, &s[2..]),
            b'b' => (2, &s[2..]),
            b'o' => (8, &s[2..]),
            _ => (10, s.as_str()),
        }
    } else {
        (10, s.as_str())
    };
    match i128::from_str_radix(slice, radix) {
        Ok(v) => Some(ConstInt::Value(v)),
        Err(e) => match e.kind() {
            std::num::IntErrorKind::PosOverflow => Some(ConstInt::PosOverflow),
            std::num::IntErrorKind::NegOverflow => Some(ConstInt::NegOverflow),
            _ => None,
        },
    }
}

#[cfg(test)]
mod unit {
    use super::*;

    #[test]
    fn ranges_match_go_fixed_width() {
        assert_eq!(fixed_width_int_range("int8"), Some((-128, 127)));
        assert_eq!(fixed_width_int_range("byte"), Some((0, 255)));
        assert_eq!(fixed_width_int_range("u16"), Some((0, 65535)));
        assert!(fixed_width_int_range("string").is_none());
        assert!(fixed_width_int_range("float64").is_none());
    }

    #[test]
    fn parse_bases() {
        assert_eq!(parse_int_lit("300"), Some(ConstInt::Value(300)));
        assert_eq!(parse_int_lit("0x12C"), Some(ConstInt::Value(300)));
        assert_eq!(parse_int_lit("0b101"), Some(ConstInt::Value(5)));
        assert_eq!(parse_int_lit("0o10"), Some(ConstInt::Value(8)));
    }

    #[test]
    fn parse_overflow_and_ranges() {
        assert_eq!(
            parse_int_lit("999999999999999999999999999999999999999999999"),
            Some(ConstInt::PosOverflow)
        );
        assert_eq!(
            parse_int_lit("0x100000000000000000000000000000000"),
            Some(ConstInt::PosOverflow)
        );

        let i64_max = ConstInt::Value(9223372036854775807);
        let i64_overflow = ConstInt::Value(9223372036854775808);
        let i64_min = ConstInt::Value(-9223372036854775808);
        let i64_underflow = ConstInt::Value(-9223372036854775809);
        let pos_overflow = ConstInt::PosOverflow;
        let neg_overflow = ConstInt::NegOverflow;

        let (lo, hi) = (i64::MIN as i128, i64::MAX as i128);
        assert!(i64_max.fits_in_range(lo, hi));
        assert!(i64_min.fits_in_range(lo, hi));
        assert!(!i64_overflow.fits_in_range(lo, hi));
        assert!(!i64_underflow.fits_in_range(lo, hi));
        assert!(!pos_overflow.fits_in_range(lo, hi));
        assert!(!neg_overflow.fits_in_range(lo, hi));
    }

    #[test]
    fn target_architecture_word_size_and_ranges() {
        assert_eq!(TARGET_INT_WIDTH_BITS, 64);
        assert_eq!(target_int_range(), (i64::MIN as i128, i64::MAX as i128));
        assert_eq!(target_uint_range(), (0, u64::MAX as i128));
        assert_eq!(fixed_width_int_range("int"), Some(target_int_range()));
        assert_eq!(fixed_width_int_range("uint"), Some(target_uint_range()));
        assert_eq!(fixed_width_int_range("isize"), Some(target_int_range()));
        assert_eq!(fixed_width_int_range("usize"), Some(target_uint_range()));
    }
}
