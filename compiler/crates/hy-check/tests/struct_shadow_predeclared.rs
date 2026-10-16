//! struct_shadow_predeclared.rs — regression for issue #326:
//! A struct named after a predeclared type (`struct rune { m map[..] }`)
//! was classified Primitive/Str because `named_shape` matched primitive
//! names before consulting `self.structs`.
//! Passing an instance of such a struct to two tasks shared mutable GC
//! state (e.g. map) across a concurrency boundary with 0 diagnostics,
//! causing runtime data races / `fatal error: concurrent map writes`.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let path = "inline.hy";
    let ast = parse_file(path, src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn struct_shadowing_rune_with_map_rejects_e3001_at_spawns() {
    let src = r#"
struct rune {
    m map[string]string
}

fn worker(h rune, id string, done chan<- string) {
    h.m[id] = id
    done <- id
}

fn fan_out(h rune, done chan<- string) {
    task worker(h, "a", done)
    task worker(h, "b", done)
}

fn main() {
    done := make(chan string, 2)
    fan_out(rune{m: map[string]string{}}, done)
    <-done
    <-done
}
"#;
    let r = check(src);
    let cs = error_codes(&r);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        cs.iter().any(|c| c == "HYB-E3001"),
        "expected HYB-E3001 at task spawns sharing struct-carrying-map, got {:?}",
        cs
    );
}

#[test]
fn struct_shadowing_string_with_map_rejects_e3001_at_spawns() {
    let src = r#"
struct string {
    m map[int]int
}

fn worker(h string, id int, done chan<- int) {
    h.m[id] = id
    done <- id
}

fn fan_out(h string, done chan<- int) {
    task worker(h, 1, done)
    task worker(h, 2, done)
}
"#;
    let r = check(src);
    let cs = error_codes(&r);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        cs.iter().any(|c| c == "HYB-E3001"),
        "expected HYB-E3001 for string-shadowing struct, got {:?}",
        cs
    );
}

#[test]
fn struct_shadowing_int_with_map_rejects_e3001_at_spawns() {
    let src = r#"
struct int {
    m map[string]int
}

fn worker(h int, id string, done chan<- int) {
    h.m[id] = 1
    done <- 1
}

fn fan_out(h int, done chan<- int) {
    task worker(h, "x", done)
}
"#;
    let r = check(src);
    let cs = error_codes(&r);
    assert_eq!(r.verdict, Verdict::Reject);
    assert!(
        cs.iter().any(|c| c == "HYB-E3001"),
        "expected HYB-E3001 for int-shadowing struct, got {:?}",
        cs
    );
}

#[test]
fn normal_unshadowed_primitives_remain_sendable() {
    let src = r#"
fn worker(r rune, s string, i int, done chan<- int) {
    done <- i
}

fn fan_out(r rune, s string, i int, done chan<- int) {
    task worker(r, s, i, done)
    task worker(r, s, i, done)
}
"#;
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept);
    assert!(
        error_codes(&r).is_empty(),
        "normal primitives must accept cleanly without diagnostics, got {:?}",
        error_codes(&r)
    );
}

#[test]
fn struct_shadowing_with_owned_move_is_accepted() {
    let src = r#"
struct rune {
    m map[string]string
}

fn worker(h rune, done chan<- string) {
    done <- "ok"
}

fn run(done chan<- string) {
    h := rune{m: map[string]string{}}
    task worker(move h, done)
}
"#;
    let r = check(src);
    assert!(
        error_codes(&r).is_empty(),
        "moving an owned shadowed struct must accept cleanly, got {:?}",
        error_codes(&r)
    );
    assert_eq!(r.verdict, Verdict::Accept);
}
