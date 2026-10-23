//! Prefixed fractional literals must be rejected before emitting Go (#1168).

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

struct Scratch(PathBuf);

impl Scratch {
    fn new(tag: &str) -> Self {
        let path = std::env::temp_dir().join(format!(
            "hyb-prefixed-fraction-{tag}-{}",
            std::process::id()
        ));
        fs::create_dir(&path).expect("create test-owned directory");
        Self(path)
    }

    fn hyb(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_hyb"))
            .args(args)
            .current_dir(&self.0)
            .output()
            .expect("run actual hyb CLI")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).expect("remove test-owned directory");
    }
}

#[test]
fn check_and_build_reject_prefixed_fractions_before_emission() {
    let dir = Scratch::new("reject");
    for literal in ["0x1.8", "0o7.5", "0b1.0", "0X1_0.8_0", "0x1.f", "0x1.fp-2"] {
        fs::write(
            dir.0.join("input.hy"),
            format!("fn main() {{\n    n := {literal}\n    fmt.println(n)\n}}\n"),
        )
        .unwrap();
        let check = dir.hyb(&["check", "input.hy", "--json"]);
        assert_eq!(check.status.code(), Some(1), "{literal}: {check:?}");
        let json: serde_json::Value = serde_json::from_slice(&check.stdout).unwrap();
        assert_eq!(json["summary"]["verdict"], "reject", "{literal}: {json}");
        assert_eq!(
            json["diagnostics"][0]["code"], "HYB-E0002",
            "{literal}: {json}"
        );
        assert_eq!(
            json["diagnostics"][0]["tier0_rule"], "LX6",
            "{literal}: {json}"
        );

        let build = dir.hyb(&["build", "input.hy", "-o", "output"]);
        assert_eq!(build.status.code(), Some(1), "{literal}: {build:?}");
        assert!(
            String::from_utf8_lossy(&build.stdout).contains("HYB-E0002"),
            "{literal}: {build:?}"
        );
        assert!(
            !dir.0.join("output.go").exists(),
            "{literal}: emitted invalid Go"
        );
        assert!(
            !dir.0.join("output").exists(),
            "{literal}: produced a binary"
        );
    }
}

#[test]
fn valid_numbers_and_prefixed_ranges_check_build_and_run() {
    let dir = Scratch::new("accept");
    fs::write(
        dir.0.join("input.hy"),
        r#"struct number_box { value int }
fn main() {
    fmt.println(0x1f, 0X1_F, 0o17, 0O1_7, 0b101, 0B1_01)
    fmt.println(1.5, 2., .5, 1e2, 2.e1)
    total := 0
    for i in 0x1..0x3 { total += i }
    for i in 0x1..=0x3 { total += i }
    for i in 0o1..0o3 { total += i }
    for i in 0o1..=0o3 { total += i }
    for i in 0b1..0b11 { total += i }
    for i in 0b1..=0b11 { total += i }
    fmt.println(total)
    fmt.println((number_box{value: 0x1}).value)
}
"#,
    )
    .unwrap();
    let check = dir.hyb(&["check", "input.hy"]);
    assert!(check.status.success(), "{check:?}");
    let build = dir.hyb(&["build", "input.hy", "-o", "output"]);
    assert!(build.status.success(), "{build:?}");
    let run = Command::new(dir.0.join("output"))
        .output()
        .expect("run numeric controls");
    assert!(run.status.success(), "{run:?}");
    assert_eq!(
        String::from_utf8(run.stdout).unwrap(),
        "31 31 15 15 5 5\n1.5 2 0.5 100 20\n27\n1\n"
    );
}
