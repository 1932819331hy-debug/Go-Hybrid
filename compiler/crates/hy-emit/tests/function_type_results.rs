//! Related to #888: function TYPE results, independently of closure lowering.
//! The current type parser accepts whitespace-separated result types, unlike
//! the parenthesized result list in a function declaration or literal.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit(src: &str) -> String {
    let file = parse_file("function_type_results.hy", src).expect("parse");
    emit_go(&file).expect("emit")
}

#[test]
fn function_type_without_results() {
    let go = emit("fn call(cb fn()) { cb() }\n");
    assert!(go.contains("func call(cb func())"), "{go}");
}

#[test]
fn function_type_with_one_result() {
    let go = emit("fn call(cb fn(x int) int) int { return cb(1) }\n");
    assert!(go.contains("func call(cb func(x int) int) int"), "{go}");
}

#[test]
fn function_type_with_multiple_results() {
    for (results, expected) in [
        ("int bool", "(int, bool)"),
        ("int bool string", "(int, bool, string)"),
    ] {
        let go = emit(&format!(
            "struct callbacks {{\n    cb fn(x int) {results}\n}}\n"
        ));
        assert!(go.contains(&format!("func(x int) {expected}")), "{go}");
    }
}

#[test]
fn function_type_with_nested_parameter() {
    let go = emit("struct callbacks {\n    cb fn(inner fn(x int) int bool) int bool\n}\n");
    assert!(
        go.contains("func(inner func(x int) (int, bool)) (int, bool)"),
        "{go}"
    );
}

#[test]
fn function_type_with_nested_results() {
    let go = emit(
        "struct single { cb fn() fn(x int) int bool }\nstruct multiple { cb fn() bool fn(x int) int bool }\n",
    );
    assert!(go.contains("func() func(x int) (int, bool)"), "{go}");
    assert!(
        go.contains("func() (bool, func(x int) (int, bool))"),
        "{go}"
    );
}

#[test]
fn function_types_compile_and_run_with_go() {
    let go = emit(
        r#"
fn ping() {}
fn identity(x int) int { return x }
fn pair(x int) (int, bool) { return x, true }

fn call_zero(cb fn()) { cb() }
fn call_single(cb fn(x int) int) int { return cb(7) }
fn call_pair(cb fn(x int) int bool) (int, bool) { return cb(11) }
fn call_nested(cb fn(inner fn(x int) int bool) int bool) (int, bool) {
    return cb(pair)
}
fn nested(inner fn(x int) int bool) (int, bool) { return inner(17) }
fn factory() fn(x int) int bool { return pair }
fn call_factory(cb fn() fn(x int) int bool) (int, bool) {
    f := cb()
    return f(13)
}

struct triple { cb fn(x int) int bool string }
struct nested_results { cb fn() bool fn(x int) int bool }

fn main() {
    call_zero(ping)
    a := call_single(identity)
    b, ok1 := call_pair(pair)
    c, ok2 := call_nested(nested)
    d, ok3 := call_factory(factory)
    fmt.println(a, b, ok1, c, ok2, d, ok3)
}
"#,
    );
    // As in the existing emitter Go test, keep shape tests usable without Go.
    if !std::process::Command::new("go")
        .arg("version")
        .output()
        .is_ok_and(|o| o.status.success())
    {
        eprintln!("skipping Go compilation: go is unavailable");
        return;
    }
    let tmp = std::env::temp_dir().join(format!(
        "hyb_function_type_results_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system clock")
            .as_nanos()
    ));
    std::fs::create_dir(&tmp).expect("create temp dir");
    std::fs::write(tmp.join("go.mod"), "module functiontypes\n\ngo 1.21\n").expect("write go.mod");
    std::fs::write(tmp.join("main.go"), &go).expect("write main.go");
    let out = std::process::Command::new("go")
        .args(["run", "."])
        .env("GOWORK", "off")
        .current_dir(&tmp)
        .output()
        .expect("invoke go run");
    std::fs::remove_dir_all(&tmp).expect("remove temp dir");
    assert!(
        out.status.success(),
        "go run failed on emitted Go:\n{go}\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&out.stdout),
        "7 11 true 17 true 13 true\n"
    );
}
