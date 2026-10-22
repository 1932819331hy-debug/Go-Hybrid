//! Native Go builds must preserve caller build flags while limiting parallelism.
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

struct Scratch(PathBuf);

impl Scratch {
    fn new(tag: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("hyb-goflags-{tag}-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn success(out: &Output) {
    assert!(
        out.status.success(),
        "stdout: {}\nstderr: {}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
}

fn exercise_build_flags(bridge: bool) {
    let dir = Scratch::new(if bridge { "bridge" } else { "plain" });
    let source = dir.0.join("main.hy");
    let program = if bridge {
        "import \"rust/sha3\"\nfn main() {\n digest := sha3.digest256(\"hybrid\")?\n fmt.println(hex.encode(digest[..8]))\n}\n"
    } else {
        "fn main() { fmt.println(\"hello\") }\n"
    };
    fs::write(&source, program).unwrap();
    for (name, flags, expected_tags) in [
        ("unset", None, None),
        ("empty", Some(""), None),
        (
            "tags",
            Some("-tags=hyb_goflags_probe"),
            Some("hyb_goflags_probe"),
        ),
        (
            "parallel",
            Some("-p=8 -tags=hyb_goflags_probe"),
            Some("hyb_goflags_probe"),
        ),
    ] {
        let binary = dir
            .0
            .join(format!("{name}{}", std::env::consts::EXE_SUFFIX));
        let mut command = Command::new(env!("CARGO_BIN_EXE_hyb"));
        command
            .arg("build")
            .arg(&source)
            .arg("-o")
            .arg(&binary)
            .current_dir(&dir.0);
        match flags {
            Some(flags) => {
                command.env("GOFLAGS", flags);
            }
            None => {
                command.env_remove("GOFLAGS");
            }
        }
        success(&command.output().unwrap());
        let metadata = Command::new("go")
            .args(["version", "-m"])
            .arg(&binary)
            .env_remove("GOFLAGS")
            .output()
            .unwrap();
        success(&metadata);
        let metadata = String::from_utf8_lossy(&metadata.stdout);
        if let Some(tags) = expected_tags {
            assert!(
                metadata.contains(&format!("-tags={tags}")),
                "caller tags missing: {metadata}"
            );
        } else {
            assert!(!metadata.contains("-tags="), "unexpected tags: {metadata}");
        }
        success(&Command::new(&binary).output().unwrap());
    }
    for (name, flags) in [
        ("unknown", "-hyb_nonexistent_flag"),
        ("invalid", "-trimpath=notabool"),
    ] {
        let binary = dir.0.join(name);
        let out = Command::new(env!("CARGO_BIN_EXE_hyb"))
            .arg("build")
            .arg(&source)
            .arg("-o")
            .arg(&binary)
            .current_dir(&dir.0)
            .env("GOFLAGS", flags)
            .output()
            .unwrap();
        assert!(
            !out.status.success(),
            "invalid GOFLAGS must not silently build"
        );
        assert!(!binary.exists(), "invalid GOFLAGS produced an artifact");
        let stderr = String::from_utf8_lossy(&out.stderr);
        if name == "invalid" {
            assert!(stderr.contains("invalid boolean value"), "{stderr}");
            assert!(stderr.contains("GOFLAGS"), "{stderr}");
        } else {
            // Unknown flags already fail the version probe, before go build.
            let stdout = String::from_utf8_lossy(&out.stdout);
            assert!(stdout.contains("HYB-E9001"), "{stdout}\n{stderr}");
        }
    }
}

#[test]
fn plain_build_preserves_goflags() {
    exercise_build_flags(false);
}

#[test]
fn bridge_build_preserves_goflags() {
    exercise_build_flags(true);
}
