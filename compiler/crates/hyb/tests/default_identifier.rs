//! Issue #1167: legal `default` identifiers must check, build, and retain
//! their binding identity. Exercise the current Cargo-built CLI and real Go.
use std::{fs, path::PathBuf, process::Command};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("hyb-default-{}-{}", name, std::process::id()));
    fs::create_dir_all(&dir).expect("create scratch");
    dir
}

fn checked_source(name: &str, source: &str) -> (PathBuf, PathBuf) {
    let dir = scratch(name);
    let src = dir.join("input.hy");
    fs::write(&src, source).unwrap();
    let check = Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("check")
        .arg(&src)
        .current_dir(&dir)
        .output()
        .unwrap();
    assert!(
        check.status.success(),
        "{name}: check failed: {}{}",
        String::from_utf8_lossy(&check.stdout),
        String::from_utf8_lossy(&check.stderr)
    );
    (dir, src)
}

fn run(name: &str, source: &str, want: &str) {
    let (dir, src) = checked_source(name, source);
    let bin = dir.join("program");
    let build = Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("build")
        .arg(&src)
        .arg("-o")
        .arg(&bin)
        .current_dir(&dir)
        .output()
        .unwrap();
    assert!(
        build.status.success(),
        "{name}: build failed: {}{}",
        String::from_utf8_lossy(&build.stdout),
        String::from_utf8_lossy(&build.stderr)
    );
    let result = Command::new(&bin).current_dir(&dir).output().unwrap();
    assert!(
        result.status.success(),
        "{name}: runtime failed: {:?}",
        result
    );
    assert_eq!(String::from_utf8(result.stdout).unwrap(), want, "{name}");
    fs::remove_dir_all(dir).unwrap();
}

macro_rules! program {
    ($name:ident, $src:expr, $output:expr) => {
        #[test]
        fn $name() {
            run(stringify!($name), $src, $output);
        }
    };
}

program!(
    local,
    r###"fn main() { default := 3
fmt.println(default) }
"###,
    "3\n"
);

program!(
    collision,
    r###"fn main() { default := 3
default_ := 4
fmt.println(default, default_) }
"###,
    "3 4\n"
);

program!(
    shadowing,
    r###"fn main() { default := 3
{ default := 4
fmt.println(default) }
fmt.println(default) }
"###,
    "4\n3\n"
);

program!(
    param,
    r###"fn read(default int) int { return default }
fn main() { fmt.println(read(3)) }
"###,
    "3\n"
);

program!(
    named_result,
    r###"fn read() (default int) { default = 3
return }
fn main() { fmt.println(read()) }
"###,
    "3\n"
);

program!(
    function,
    r###"fn default() int { return 3 }
fn main() { fmt.println(default()) }
"###,
    "3\n"
);

program!(
    struct_name,
    r###"struct default { value int }
fn main() { x := default{value: 3}
fmt.println(x.value) }
"###,
    "3\n"
);

program!(
    field,
    r###"struct record { default int }
fn main() { x := record{default: 3}
x.default = 4
fmt.println(x.default) }
"###,
    "4\n"
);

program!(
    method,
    r###"struct record { value int }
fn (r record) default() int { return r.value }
fn main() { x := record{value: 3}
fmt.println(x.default()) }
"###,
    "3\n"
);

program!(
    receiver,
    r###"struct record { value int }
fn (default record) read() int { return default.value }
fn main() { x := record{value: 3}
fmt.println(x.read()) }
"###,
    "3\n"
);

program!(
    zero_init_field,
    r###"struct record { default map[string]int }
fn main() { var x record
x.default["a"] = 3
fmt.println(x.default["a"]) }
"###,
    "3\n"
);

program!(
    closure,
    r###"fn main() { f := fn(default int) int { return default }
fmt.println(f(3)) }
"###,
    "3\n"
);

program!(
    range,
    r###"fn main() { for default in 0..2 { fmt.println(default) } }
"###,
    "0\n1\n"
);

program!(
    iterate,
    r###"fn main() { xs := []int{3}
for default in xs { fmt.println(default) } }
"###,
    "3\n"
);

program!(
    match_name,
    r###"fn main() { x := some(3)
match x { some(default): fmt.println(default)
none: fmt.println(0) } }
"###,
    "3\n"
);

program!(
    select,
    r###"fn main() { ch := make(chan int, 1)
ch <- 3
select { default := <-ch: fmt.println(default)
default: fmt.println(0) } }
"###,
    "3\n"
);

program!(
    array_const,
    r###"const default = 3
fn main() { var x [default]int
fmt.println(len(x)) }
"###,
    "3\n"
);

program!(
    generic_type,
    r###"struct box[default] { value default }
fn main() { x := box[int]{value: 3}
fmt.println(x.value) }
"###,
    "3\n"
);

program!(
    string_control,
    r###"fn main() { default_ := 4
fmt.println("default", default_) }
"###,
    "default 4\n"
);

program!(
    select_control,
    r###"fn main() { ch := make(chan int)
select { <-ch: fmt.println(1)
default: fmt.println(3) } }
"###,
    "3\n"
);

program!(
    channel,
    r###"fn main() {
    default := make(chan int, 1)
    default <- 7
    v := <-default
    fmt.println("{v}")
}

"###,
    "7\n"
);

program!(
    collision_neighbors,
    r###"fn main() { default := 1
default_ := 2
default__ := 3
hy_keyword_default := 4
fmt.println(default, default_, default__, hy_keyword_default) }
"###,
    "1 2 3 4\n"
);

program!(
    alias_types,
    r###"type default = int
fn read(x default) default { return x }
fn main() { var x default = 3
p := new(default)
fmt.println(read(x), p == nil) }
"###,
    "3 false\n"
);

program!(
    generic_function,
    r###"fn default[t](x t) t { return x }
fn main() { fmt.println(default[int](3)) }
"###,
    "3\n"
);

program!(
    loan_storeback,
    r###"struct record { value int }
fn main() { m := map[string]record{"a": {value: 1}}
{ default := &m["a"]
default.value = 3 }
fmt.println(m["a"].value) }
"###,
    "3\n"
);

program!(
    var_const_assignment,
    r###"fn main() { var default int = 1
const default_ = 2
default, default__ := default + default_, 4
default += default__
fmt.println(default) }
"###,
    "7\n"
);

program!(
    if_init,
    r###"fn main() { default := 2
if default := default + 1; default > 0 { fmt.println(default) }
fmt.println(default) }
"###,
    "3\n2\n"
);

program!(
    closure_result,
    r###"fn main() { f := fn() (default int) { default = 3
return }
fmt.println(f()) }
"###,
    "3\n"
);

program!(
    function_type,
    r###"fn read(f fn(default int) int) int { return f(3) }
fn main() { fmt.println(read(fn(x int) int { return x })) }
"###,
    "3\n"
);

program!(
    match_guards,
    r###"fn main() { x := some(3)
match x { some(default) if default < 0: fmt.println(0)
some(default) if default == 3: fmt.println(default)
some(default): fmt.println(2)
none: fmt.println(1) } }
"###,
    "3\n"
);

program!(
    select_two,
    r###"fn main() { ch := make(chan int, 1)
ch <- 3
select { default, ok := <-ch: fmt.println(default, ok)
default: fmt.println(0) } }
"###,
    "3 true\n"
);

program!(
    iterator_two,
    r###"fn main() { xs := []int{3}
for default, default_ in xs { fmt.println(default, default_) } }
"###,
    "0 3\n"
);

program!(
    error_result_zero,
    r###"struct default { value int }
fn read() (default, error) { n := strconv.atoi("3")?
return default{value: n}, nil }
fn main() { x := read()?
fmt.println(x.value) }
"###,
    "3\n"
);

program!(
    error_binding,
    r###"fn main() { default := strconv.atoi("3")?
fmt.println(default) }
"###,
    "3\n"
);

program!(
    main_result,
    r###"fn main() (default int) { default = 0
return }
"###,
    ""
);

program!(
    associated_function,
    r###"struct default { value int }
fn default.new(x int) default { return default{value: x} }
fn main() { x := default.new(3)
fmt.println(x.value) }
"###,
    "3\n"
);

program!(
    foreign_receiver,
    r###"fn main() { default := time.now()
fmt.println(default.is_zero()) }
"###,
    "false\n"
);

#[test]
fn generated_identifier_is_not_valid_source() {
    for spelling in ["hyKeywordDefault", "HYKEYWORDDEFAULT"] {
        let dir = scratch(spelling);
        let src = dir.join("invalid.hy");
        fs::write(
            &src,
            format!("fn main() {{ {spelling} := 1\nfmt.println({spelling}) }}"),
        )
        .unwrap();
        let result = Command::new(env!("CARGO_BIN_EXE_hyb"))
            .arg("check")
            .arg(&src)
            .output()
            .unwrap();
        assert!(!result.status.success());
        let diagnostics = format!(
            "{}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        assert!(diagnostics.contains("HYB-E0001"), "{diagnostics}");
        fs::remove_dir_all(dir).unwrap();
    }
}

fn package(name: &str, source: &str, test: &str) {
    let (dir, src) = checked_source(name, source);
    let pkg = dir.join("pkg");
    let build = Command::new(env!("CARGO_BIN_EXE_hyb"))
        .arg("build")
        .arg(&src)
        .args(["--package", "fixture", "--out-dir"])
        .arg(&pkg)
        .current_dir(&dir)
        .output()
        .unwrap();
    assert!(build.status.success(), "{:?}", build);
    fs::write(pkg.join("fixture_test.go"), test).unwrap();
    let result = Command::new("go")
        .args(["test", "fixture.go", "fixture_test.go"])
        .current_dir(&pkg)
        .output()
        .expect("run installed Go");
    assert!(
        result.status.success(),
        "{name}: Go test failed: {}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn package_public_default_keeps_api_and_lexical_bindings() {
    package(
        "public",
        r#"
pub fn default() int { return 3 }
pub fn check() int {
    first := default()
    { default := fn() int { return 4 }
      first += default() }
    after := default()
    captured := fn() int { return default() }
    local := fn(default int) int { return default }
    return first + after + captured() + local(5)
}
pub fn shadow(default int) int { return default }
"#,
        r#"package fixture
import "testing"
func TestAPI(t *testing.T) {
    if Default() != 3 || Check() != 18 || Shadow(8) != 8 { t.Fatal("binding or API changed") }
}
"#,
    );
}

#[test]
fn package_generic_default_keeps_exported_name() {
    package(
        "generic-public",
        r#"
pub fn default[t](x t) t { return x }
pub fn check() int { return default[int](3) }
"#,
        r#"package fixture
import "testing"
func TestAPI(t *testing.T) {
    if Default[int](5) != 5 || Check() != 3 { t.Fatal("generic API changed") }
}
"#,
    );
}

program!(
    handler_error_writer,
    r#"fn main() {
    h := http.handler(fn(default, r) {
        n := strconv.atoi("bad")?
        fmt.println(n)
    })
    code, body := httptest.get(h, "/")?
    fmt.println(code, len(body) > 0)
}
"#,
    "400 true\n"
);

program!(
    alias_make,
    r#"type default = chan int
fn main() { ch := make(default, 1)
ch <- 3
fmt.println(<-ch) }
"#,
    "3\n"
);

program!(
    loan_reassignment,
    r#"struct record { value int }
fn main() { m := map[string]record{"a": {value: 1}}
{ var default record
default = &m["a"]
default.value = 3 }
fmt.println(m["a"].value) }
"#,
    "3\n"
);

program!(
    method_value,
    r#"struct record { value int }
fn (r record) default() int { return r.value }
fn main() { x := record{value: 3}
f := x.default
fmt.println(f()) }
"#,
    "3\n"
);

#[test]
fn package_file_scope_does_not_inherit_parameter_shadow() {
    package(
        "file-scope",
        r#"
pub fn default() int { return 3 }
fn shadow(default int) int { return default }
const callbacks = []fn() int{default}
pub fn check() int { return callbacks[0]() }
"#,
        r#"package fixture
import "testing"
func TestAPI(t *testing.T) {
    if Default() != 3 || Check() != 3 { t.Fatal("file scope inherited a parameter") }
}
"#,
    );
}

#[test]
fn package_type_parameter_shadows_exported_default() {
    package(
        "type-parameter",
        r#"
pub fn default(x int) int { return x }
pub fn identity[default](x default) default { return default(x) }
"#,
        r#"package fixture
import "testing"
func TestAPI(t *testing.T) {
    if Default(3) != 3 || Identity[string]("ok") != "ok" { t.Fatal("type parameter binding changed") }
}
"#,
    );
}

#[test]
fn package_control_flow_restores_exported_default() {
    package(
        "control-scope",
        r#"
pub fn default() int { return 3 }
pub fn check() int {
    total := default()
    if default := 1; default > 0 { total += default }
    total += default()
    for default in 0..2 { total += default }
    total += default()
    match some(2) {
        some(default) => { total += default }
        none => {}
    }
    total += default()
    ch := make(chan int, 1)
    ch <- 2
    select { default := <-ch: total += default }
    return total + default()
}
"#,
        r#"package fixture
import "testing"
func TestAPI(t *testing.T) {
    if Check() != 21 { t.Fatal(Check()) }
}
"#,
    );
}
