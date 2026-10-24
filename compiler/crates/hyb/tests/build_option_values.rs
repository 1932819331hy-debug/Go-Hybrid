//! Value-taking build options must not consume the following option as a value.

use std::process::Command;

fn rejects_following_option(option: &str) {
    for next in [
        "--emit-go",
        "--unknown-option",
        "--package",
        "--out-dir",
        "--binding-prefix",
        "-o",
        "--",
    ] {
        // A nonexistent source makes the diagnostic distinguish argument validation
        // from a later file-read failure, without compiling or writing any output.
        let file = std::env::temp_dir().join(format!(
            "hyb-build-option-values-missing-{}.hy",
            std::process::id()
        ));
        assert!(!file.exists(), "test input must not exist");
        let package_args: &[&str] = match option {
            "--package" => &["--out-dir", "output"],
            "--out-dir" => &["--package", "demo"],
            "--binding-prefix" => &["--package", "demo", "--out-dir", "output"],
            _ => &[],
        };
        let output = Command::new(env!("CARGO_BIN_EXE_hyb"))
            .arg("build")
            .arg(&file)
            .args(package_args)
            .args([option, next])
            .env("HYB_ALLOW_TOOLCHAIN_DRIFT", "1")
            .output()
            .expect("run actual hyb CLI");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(2), "{option} {next}: {stderr}");
        assert!(output.stdout.is_empty(), "{option} {next}: {output:?}");
        assert!(
            stderr.contains(&format!("option {option} requires an argument")),
            "{option} {next}: {stderr}"
        );
        assert!(stderr.contains("usage: hyb build"), "{stderr}");
        assert!(!stderr.contains("error reading"), "{stderr}");
    }
}

#[test]
fn output_path_cannot_be_an_option() {
    rejects_following_option("-o");
}

#[test]
fn package_name_cannot_be_an_option() {
    rejects_following_option("--package");
}

#[test]
fn output_directory_cannot_be_an_option() {
    rejects_following_option("--out-dir");
}

#[test]
fn binding_prefix_cannot_be_an_option() {
    rejects_following_option("--binding-prefix");
}

#[test]
fn valid_values_still_reach_the_source_file() {
    let file = std::env::temp_dir().join(format!(
        "hyb-build-option-values-missing-{}.hy",
        std::process::id()
    ));
    assert!(!file.exists(), "test input must not exist");
    for args in [
        vec!["-o", "ordinary output"],
        vec!["-o", "-output"],
        vec!["-o", "-"],
        vec!["-o", "./--emit-go"],
        vec!["--package", "demo", "--out-dir", "./--output"],
        vec![
            "--package",
            "demo",
            "--out-dir",
            "output",
            "--binding-prefix",
            "example.com/my-package",
        ],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_hyb"))
            .arg("build")
            .arg(&file)
            .args(&args)
            .env("HYB_ALLOW_TOOLCHAIN_DRIFT", "1")
            .output()
            .expect("run actual hyb CLI");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(2), "{args:?}: {stderr}");
        assert!(output.stdout.is_empty(), "{args:?}: {output:?}");
        assert!(stderr.contains("error reading"), "{args:?}: {stderr}");
        assert!(!stderr.contains("requires an argument"), "{stderr}");
    }
}
