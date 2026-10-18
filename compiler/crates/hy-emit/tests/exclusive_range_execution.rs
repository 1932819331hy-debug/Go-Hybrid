//! End-to-end execution regression for issue #730.
//! Golden emitter strings alone cannot prove range semantics: compile and run
//! the emitted Go, then compare with the original issue's expected output.

use hy_emit::emit_go;
use hy_syntax::parse_file;
use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn exclusive_ranges_evaluate_bounds_once_in_enclosing_scope() {
    let source = r#"package main

fn main() {
    n := 3
    for n in 0..n {
        fmt.println("exclusive {n}")
    }
    for n in 0..=n - 1 {
        fmt.println("inclusive {n}")
    }

    limit := 2
    for i in 0..limit {
        limit = 5
        fmt.println("grow {i}")
    }

    calls := 0
    bound := fn() int {
        calls += 1
        return 2
    }
    for i in 0..bound() {
        fmt.println("call {i}")
    }
    fmt.println("bound() ran {calls} times")

    var upper int8 = 127
    var lower int8 = 126
    for v in lower..upper {
        fmt.println("typed {v}")
    }
    for v in upper..upper {
        fmt.println("unreachable {v}")
    }
}
"#;

    let ast = parse_file("issue-730.hy", source).expect("repro should parse");
    let emitted = emit_go(&ast).expect("repro should emit Go");

    // Go is a pinned prerequisite for the compiler/differential CI job.
    // Use a unique temporary directory so parallel Rust tests never collide.
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "hy-emit-issue-730-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir(&dir).expect("create isolated Go workdir");
    let go_file = dir.join("main.go");
    fs::write(&go_file, &emitted).expect("write generated Go");
    let run = Command::new("go")
        .arg("run")
        .arg("main.go")
        .current_dir(&dir)
        .env("GO111MODULE", "off")
        .env("GOWORK", "off")
        .output();
    let _ = fs::remove_dir_all(&dir);
    let run = run.expect("Go is required for the end-to-end compiler suite");
    assert!(
        run.status.success(),
        "generated Go failed (status {}):\n{}\n{}",
        run.status,
        String::from_utf8_lossy(&run.stderr),
        emitted
    );
    let actual = String::from_utf8(run.stdout).expect("stdout is UTF-8");
    assert_eq!(
        actual.replace("\r\n", "\n"),
        concat!(
            "exclusive 0\n",
            "exclusive 1\n",
            "exclusive 2\n",
            "inclusive 0\n",
            "inclusive 1\n",
            "inclusive 2\n",
            "grow 0\n",
            "grow 1\n",
            "call 0\n",
            "call 1\n",
            "bound() ran 1 times\n",
            "typed 126\n",
        ),
        "the accepted Hybrid program must preserve evaluate-once range semantics"
    );
}
