//! Issue #645: N5's first-write map allocation, run end-to-end through
//! `hyb build`.
//!
//! The accept-side corpus program carries its stdout as
//! `// EXPECTED-OUTPUT:` header lines (one per line, the corpus 35 marker);
//! the differential loop only checks that an accepted program builds and
//! exits 0, so this test diffs the output byte-exact.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const CORPUS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../tests/checker-corpus");

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("hyb-{}-{}", tag, std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

/// `hyb build <src> -o <dir>/prog`, then run it.
fn build_and_run(src: &Path, dir: &Path) -> Output {
    let bin = dir.join("prog");
    let build = Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("build")
        .arg(src)
        .arg("-o")
        .arg(&bin)
        .current_dir(dir)
        .output()
        .expect("run hyb build");
    assert!(
        build.status.success(),
        "hyb build of {} failed\nstdout: {}\nstderr: {}",
        src.display(),
        String::from_utf8_lossy(&build.stdout),
        String::from_utf8_lossy(&build.stderr)
    );
    Command::new(&bin).output().expect("run the built program")
}

/// The `// EXPECTED-OUTPUT: <line>` header lines, joined as stdout.
fn expected_output(src: &str) -> String {
    let mut out = String::new();
    for line in src.lines() {
        if let Some(rest) = line.strip_prefix("// EXPECTED-OUTPUT: ") {
            out.push_str(rest);
            out.push('\n');
        }
    }
    out
}

fn corpus_output_is_byte_exact(name: &str) {
    let src = Path::new(CORPUS).join(name);
    let text = fs::read_to_string(&src).expect("read corpus program");
    let want = expected_output(&text);
    assert!(!want.is_empty(), "{name} must carry EXPECTED-OUTPUT lines");

    let dir = scratch(name.trim_end_matches(".hy"));
    let run = build_and_run(&src, &dir);
    let _ = fs::remove_dir_all(&dir);
    assert!(
        run.status.success(),
        "{name} exited {:?}\nstderr: {}",
        run.status,
        String::from_utf8_lossy(&run.stderr)
    );
    assert_eq!(
        String::from_utf8(run.stdout).expect("UTF-8 stdout"),
        want,
        "{name}: stdout disagrees with its EXPECTED-OUTPUT header"
    );
}

#[test]
fn n5_zero_map_corpus_program_output() {
    corpus_output_is_byte_exact("n5-zero-map-first-write.hy");
}

#[test]
fn expected_output_reads_one_line_per_marker() {
    let src = "// EXPECTED-VERDICT: ACCEPT\n// EXPECTED-OUTPUT: true\n// EXPECTED-OUTPUT: x 3\nfn main() {}\n";
    assert_eq!(expected_output(src), "true\nx 3\n");
}

#[test]
fn parenthesized_new_maps_are_writable_from_go() {
    // Hybrid has no general `*p` expression: exercise the returned map pointer
    // through its supported Go-package interop, without editing emitted code.
    let dir = scratch("n5-parenthesized-map");
    fs::write(
        dir.join("allocation.hy"),
        "type counts = map[string]int\ntype chain = counts\ntype t = map[string]int\n\
         struct bag { items map[string]int }\nstruct box[t any] { value t }\n\
         pub fn alias() *counts { return new((counts)) }\n\
         pub fn nested() *chain { return new((((chain)))) }\n\
         pub fn direct() *map[string]int { return new((map[string]int{})) }\n\
         pub fn ordinary() *counts { return new(counts) }\n\
         pub fn structure() *bag { return new(bag) }\n\
         pub fn generic[counts any]() *counts { return new((counts)) }\n\
         fn (b *box[t]) allocate() *t { return new((t)) }\n\
         pub fn local_value() *int {\n counts := 7\n _ = counts\n return new((counts))\n }\n\
         pub fn local_new() int {\n new := fn(n int) int { return n + 1 }\n counts := 7\n _ = counts\n return new((counts))\n }\n",
    )
    .unwrap();
    fs::write(
        dir.join("main.go"),
        r#"package main
func main() {
    maps := []*map[string]int{Alias(), Nested(), Direct(), Ordinary(), &Structure().items}
    for i, p := range maps {
        if len(*p) != 0 || (*p)["missing"] != 0 { panic("not the empty zero value") }
        (*p)["first"] += i + 1
        if (*p)["first"] != i + 1 { panic("first write lost") }
    }
    first, second := Alias(), Alias()
    (*first)["independent"] = 9
    if len(*second) != 0 { panic("allocations unexpectedly shared") }
    if *Generic[int]() != 0 || *LocalValue() != 7 || LocalNew() != 8 { panic("scope changed") }
    b := box[int]{}
    if *b.allocate() != 0 { panic("receiver type parameter changed") }
}
"#,
    )
    .unwrap();
    let generate = Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("generate")
        .arg(&dir)
        .output()
        .expect("run hyb generate");
    assert!(
        generate.status.success(),
        "generate failed: {}{}",
        String::from_utf8_lossy(&generate.stdout),
        String::from_utf8_lossy(&generate.stderr)
    );
    let bin = dir.join("program");
    let build = Command::new("go")
        .args(["build", "-o"])
        .arg(&bin)
        .arg(dir.join("allocation.hy.go"))
        .arg(dir.join("main.go"))
        .output()
        .expect("run go build");
    assert!(
        build.status.success(),
        "go build failed: {}",
        String::from_utf8_lossy(&build.stderr)
    );
    let run = Command::new(&bin)
        .output()
        .expect("run generated allocation");
    assert!(
        run.status.success(),
        "first-write/scope regression: {}",
        String::from_utf8_lossy(&run.stderr)
    );
    let _ = fs::remove_dir_all(&dir);
}
