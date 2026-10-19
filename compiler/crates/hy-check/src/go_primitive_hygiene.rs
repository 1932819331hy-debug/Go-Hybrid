//! Issue #728: prevent generated Go numeric primitive names from being shadowed.
//!
//! Hybrid spells numeric types f64, i64, u8, etc. Their Go spellings are
//! float64, int64, uint8, etc. If a source binding uses the Go spelling,
//! the emitted numeric conversion can instead call the user function or
//! variable, producing wrong results or invalid Go. Reject declarations
//! of just these collisions, not arbitrary foreign names or selectors.

use hy_diag::{Diagnostic, Span};
use hy_syntax::{Block, Decl, Expr, File, ForKind, InterpPart, MatchPattern, SelectArmKind, Stmt, TypeExpr};

fn conflicts_with_emitted_numeric_type(name: &str) -> bool {
    matches!(
        name,
        "int8" | "int16" | "int32" | "int64"
            | "uint8" | "uint16" | "uint32" | "uint64"
            | "float32" | "float64"
    )
}

fn check_binding(file: &File, name: &str, span: Span, out: &mut Vec<Diagnostic>) {
    if !conflicts_with_emitted_numeric_type(name) {
        return;
    }
    out.push(
        Diagnostic::error(
            "HYB-E1002",
            file.path.clone(),
            span,
            format!(
                "binding named '{}' would shadow an emitted Go primitive type (HYB-E1002)",
                name
            ),
        )
        .with_notes(vec![
            format!(
                "why: Hybrid numeric conversions are lowered to Go '{}' and \
                 the user binding can hijack them, silently changing arithmetic or breaking go build (issue #728)",
                name
            ),
            format!(
                "fix: rename the binding '{}' (e.g. user_{}) and leave \
                 the source numeric conversions as f64(...), i64(...), u8(...), etc.",
                name, name
            ),
        ]),
    );
}

fn check_type(file: &File, ty: &TypeExpr, out: &mut Vec<Diagnostic>) {
    match ty {
        TypeExpr::Fn { params, results, span } => {
            for p in params {
                check_binding(file, &p.name, p.span, out);
                check_type(file, &p.ty, out);
            }
            for r in results {
                check_type(file, r, out);
            }
            let _ = span;
        }
        TypeExpr::Pointer { to, .. } => check_type(file, to, out),
        TypeExpr::Slice { elem, .. }
        | TypeExpr::Chan { elem, .. }
        | TypeExpr::Option { elem, .. } => check_type(file, elem, out),
        TypeExpr::Array { len, elem, .. } => {
            if let Some(n) = len {
                check_expr(file, n, out);
            }
            check_type(file, elem, out);
        }
        TypeExpr::Map { key, value, .. } => {
            check_type(file, key, out);
            check_type(file, value, out);
        }
        TypeExpr::Named { .. } => {}
    }
}

fn check_expr(file: &File, expr: &Expr, out: &mut Vec<Diagnostic>) {
    match expr {
        Expr::FnLit { params, results, body, span } => {
            for p in params {
                check_binding(file, &p.name, p.span, out);
                check_type(file, &p.ty, out);
            }
            for r in results {
                if let Some(n) = &r.name {
                    check_binding(file, n, *span, out);
                }
                check_type(file, &r.ty, out);
            }
            check_block(file, body, out);
        }
        Expr::Binary { lhs, rhs, .. } | Expr::Index { base: lhs, index: rhs, .. } => {
            check_expr(file, lhs, out);
            check_expr(file, rhs, out);
        }
        Expr::Unary { operand, .. }
        | Expr::AddrOf { operand, .. }
        | Expr::Move { operand, .. }
        | Expr::Try { operand, .. }
        | Expr::Paren { inner: operand, .. }
        | Expr::Receive { chan: operand, .. } => check_expr(file, operand, out),
        Expr::Slice { base, lo, hi, .. } => {
            check_expr(file, base, out);
            if let Some(x) = lo { check_expr(file, x, out); }
            if let Some(x) = hi { check_expr(file, x, out); }
        }
        Expr::Field { base, .. } => check_expr(file, base, out),
        Expr::Call { callee, args, .. } => {
            check_expr(file, callee, out);
            for arg in args { check_expr(file, &arg.value, out); }
        }
        Expr::StructLit { ty, fields, .. } => {
            if let Some(t) = ty { check_type(file, t, out); }
            for f in fields { check_expr(file, &f.value, out); }
        }
        Expr::SliceLit { elem, items, .. } => {
            if let Some(t) = elem { check_type(file, t, out); }
            for x in items { check_expr(file, x, out); }
        }
        Expr::MapLit { key, value, entries, .. } => {
            check_type(file, key, out);
            check_type(file, value, out);
            for (k, v) in entries {
                check_expr(file, k, out);
                check_expr(file, v, out);
            }
        }
        Expr::Interp { parts, .. } => {
            for p in parts {
                if let InterpPart::Expr(e) = p { check_expr(file, e, out); }
            }
        }
        Expr::Ident { .. } | Expr::Lit { .. } => {}
    }
}

fn check_stmt(file: &File, stmt: &Stmt, out: &mut Vec<Diagnostic>) {
    match stmt {
        Stmt::Local(l) => {
            for n in &l.names { check_binding(file, n, l.span, out); }
            if let Some(t) = &l.ty { check_type(file, t, out); }
            for v in &l.values { check_expr(file, v, out); }
        }
        Stmt::Assign { targets, values, .. } => {
            for p in targets {
                // Index expressions can contain nested function literals.
                check_place(file, p, out);
            }
            for v in values { check_expr(file, v, out); }
        }
        Stmt::Expr { expr, .. } => check_expr(file, expr, out),
        Stmt::Send { chan, value, .. } => {
            check_expr(file, chan, out);
            check_expr(file, value, out);
        }
        Stmt::If { init, cond, then, otherwise, .. } => {
            if let Some(s) = init { check_stmt(file, s, out); }
            check_expr(file, cond, out);
            check_block(file, then, out);
            if let Some(s) = otherwise { check_stmt(file, s, out); }
        }
        Stmt::For { kind, body, span } => {
            match kind {
                ForKind::Range { var, lo, hi, .. } => {
                    check_binding(file, var, *span, out);
                    check_expr(file, lo, out);
                    check_expr(file, hi, out);
                }
                ForKind::Iterate { vars, expr } => {
                    for n in vars { check_binding(file, n, *span, out); }
                    check_expr(file, expr, out);
                }
                ForKind::While { cond } => check_expr(file, cond, out),
                ForKind::Infinite => {}
            }
            check_block(file, body, out);
        }
        Stmt::Return { values, .. } => {
            for v in values { check_expr(file, v, out); }
        }
        Stmt::Task { callee, args, .. } => {
            check_expr(file, callee, out);
            for a in args { check_expr(file, &a.value, out); }
        }
        Stmt::Managed { body, .. } | Stmt::Block { block: body, .. } => {
            check_block(file, body, out)
        }
        Stmt::Match { subject, arms, span } => {
            check_expr(file, subject, out);
            for arm in arms {
                if let MatchPattern::Path { binding: Some(n), .. } = &arm.pattern {
                    check_binding(file, n, *span, out);
                }
                if let Some(g) = &arm.guard { check_expr(file, g, out); }
                check_block(file, &arm.body, out);
            }
        }
        Stmt::Select { arms, span } => {
            for arm in arms {
                match &arm.kind {
                    SelectArmKind::Recv { names, chan } => {
                        for n in names { check_binding(file, n, *span, out); }
                        check_expr(file, chan, out);
                    }
                    SelectArmKind::Send { chan, value, .. } => {
                        check_expr(file, chan, out);
                        check_expr(file, value, out);
                    }
                    SelectArmKind::Default => {}
                }
                check_block(file, &arm.body, out);
            }
        }
        Stmt::Defer { call, .. } => check_expr(file, call, out),
        Stmt::Break { .. } | Stmt::Continue { .. } => {}
    }
}

fn check_place(file: &File, place: &hy_syntax::Place, out: &mut Vec<Diagnostic>) {
    match &place.kind {
        hy_syntax::PlaceKind::Index { base, index } => {
            check_place(file, base, out);
            check_expr(file, index, out);
        }
        hy_syntax::PlaceKind::Field { base, .. } | hy_syntax::PlaceKind::AddrOf { base } => {
            check_place(file, base, out);
        }
        hy_syntax::PlaceKind::Ident(_) => {}
    }
}

fn check_block(file: &File, block: &Block, out: &mut Vec<Diagnostic>) {
    for s in &block.stmts { check_stmt(file, s, out); }
}

/// Inspect all declaration sites, including closures. Selector field names
/// are deliberately NOT prohibited; they do not occupy Go's type namespace.
pub(crate) fn check_go_primitive_shadowing(file: &File) -> Vec<Diagnostic> {
    let mut out = Vec::new();
    for decl in &file.decls {
        match decl {
            Decl::Fn(f) => {
                if f.receiver.is_none() {
                    check_binding(file, &f.name, f.span, &mut out);
                }
                if let Some(r) = &f.receiver {
                    check_binding(file, &r.name, f.span, &mut out);
                    check_type(file, &r.ty, &mut out);
                }
                for t in &f.type_params {
                    check_binding(file, &t.name, f.span, &mut out);
                }
                for p in &f.params {
                    check_binding(file, &p.name, p.span, &mut out);
                    check_type(file, &p.ty, &mut out);
                }
                for r in &f.results {
                    if let Some(n) = &r.name {
                        check_binding(file, n, f.span, &mut out);
                    }
                    check_type(file, &r.ty, &mut out);
                }
                if let Some(b) = &f.body { check_block(file, b, &mut out); }
            }
            Decl::Const(c) => {
                check_binding(file, &c.name, c.span, &mut out);
                if let Some(t) = &c.ty { check_type(file, t, &mut out); }
                check_expr(file, &c.value, &mut out);
            }
            Decl::Struct(s) => {
                check_binding(file, &s.name, s.span, &mut out);
                for t in &s.type_params { check_binding(file, &t.name, s.span, &mut out); }
                for f in &s.fields { check_type(file, &f.ty, &mut out); }
            }
            Decl::TypeAlias(a) => {
                check_binding(file, &a.name, a.span, &mut out);
                check_type(file, &a.ty, &mut out);
            }
            Decl::Enum(e) => check_binding(file, &e.name, e.span, &mut out),
            Decl::Import { .. } => {}
        }
    }
    out
}
