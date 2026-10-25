//! n5_zero_map_lowering.rs — emission pins for issue #645.
//!
//! SPEC-LANGUAGE N5: a Hybrid-declared map's zero value allocates on first
//! write, where Go panics on a nil map. The emitter used to lower every zero
//! map Go-exact; it now allocates zero maps where they are declared: `var`
//! locals, struct literals that leave a map field unkeyed (nested structs
//! included), and `new(T)`.
//!
//! The byte-exact runtime behaviour is pinned by the corpus program
//! `n5-zero-map-first-write.hy` (hyb/tests/spec_n5_zero_maps.rs).

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("test.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn n5_var_zero_map_allocates_at_declaration() {
    let go = emit_src(
        "type tally = map[string]int\n\nfn main() {\n    var m map[string]int\n    m[\"a\"] = 1\n    var t tally\n    t[\"b\"] = 2\n    var n int\n    fmt.println(len(m) + len(t) + n)\n}\n",
    );
    assert!(
        go.contains("var m map[string]int = map[string]int{}"),
        ":\n{go}"
    );
    assert!(go.contains("var t tally = tally{}"), ":\n{go}");
    // A type whose Go zero value already is Hybrid's stays untouched.
    assert!(go.contains("var n int\n"), ":\n{go}");
}

#[test]
fn n5_struct_zero_values_allocate_map_fields() {
    let go = emit_src(
        "\
struct bag {
    label  string
    counts map[string]int
}

struct shelf {
    top bag
    n   int
}

struct plain {
    n int
}

fn main() {
    var b bag
    keyed := bag{label: \"x\"}
    given := bag{counts: map[string]int{\"k\": 1}}
    var s shelf
    p := new(bag)
    var q plain
    q2 := plain{}
    bags := []bag{{label: \"e\"}}
    fmt.println(len(b.counts) + len(keyed.counts) + len(given.counts) + len(s.top.counts) + len(p.counts) + q.n + q2.n + len(bags))
}
",
    );
    for want in [
        "var b bag = bag{counts: map[string]int{}}",
        "keyed := bag{label: \"x\", counts: map[string]int{}}",
        // A keyed map field is the literal's own value — never doubled.
        "given := bag{counts: map[string]int{\"k\": 1}}",
        "var s shelf = shelf{top: bag{counts: map[string]int{}}}",
        "p := &bag{counts: map[string]int{}}",
        "var q plain\n",
        "q2 := plain{}",
        "bags := []bag{bag{label: \"e\", counts: map[string]int{}}}",
    ] {
        assert!(go.contains(want), "missing {want:?} in:\n{go}");
    }
}

#[test]
fn n5_leaves_generic_structs_alone() {
    // Generic struct fields need the literal's type arguments substituted;
    // they keep Go's zero value (a documented limit of this lowering).
    let go = emit_src(
        "\
struct box[t comparable] {
    m map[t]int
}

fn main() {
    b := box[int]{}
    fmt.println(len(b.m))
}
",
    );
    assert!(go.contains("b := box[int]{}"), ":\n{go}");
}

#[test]
fn n5_parenthesized_map_operands_allocate() {
    let go = emit_src(
        "type counts = map[string]int\ntype chain = counts\n\
         fn alias() *counts { return new((counts)) }\n\
         fn nested() *chain { return new((((chain)))) }\n\
         fn direct() *map[string]int { return new((map[string]int{})) }\n\
         fn ordinary() *counts { return new(counts) }\n",
    );
    for want in [
        "return &counts{}",
        "return &chain{}",
        "return &map[string]int{}",
    ] {
        assert!(go.contains(want), "missing {want:?} in:\n{go}");
    }
    assert!(
        !go.contains("new(counts)"),
        "map alias left unallocated:\n{go}"
    );
    assert!(
        !go.contains("new(chain)"),
        "alias chain left unallocated:\n{go}"
    );
}

#[test]
fn n5_parenthesized_map_names_respect_value_and_type_parameter_scopes() {
    let go = emit_src(
        "type counts = map[string]int\ntype t = map[string]int\n\
         struct box[t any] { value t }\n\
         fn generic[counts any]() *counts { return new((counts)) }\n\
         fn (b *box[t]) allocate() *t { return new((t)) }\n\
         fn local() *int {\n counts := 7\n _ = counts\n return new((counts))\n }\n\
         fn shadowed_new() int {\n new := fn(n int) int { return n + 1 }\n counts := 7\n _ = counts\n return new((counts))\n }\n",
    );
    assert!(
        go.contains("return new(counts)"),
        "shadowed alias changed:\n{go}"
    );
    assert!(
        go.contains("return new(t)"),
        "receiver type parameter changed:\n{go}"
    );
    assert!(
        !go.contains("&counts{"),
        "scope resolved as package alias:\n{go}"
    );
    assert!(
        !go.contains("&t{"),
        "receiver scope resolved as package alias:\n{go}"
    );
}

#[test]
fn n5_parenthesized_non_maps_and_non_type_arguments_keep_existing_lowering() {
    let go = emit_src(
        "type counts = map[string]int\nstruct bag { items map[string]int }\nstruct plain { n int }\n\
         fn main() {\n\
         a := new(bag)\n b := new((plain))\n c := new((int))\n\
         d := new(value=(counts))\n e := new((counts)...)\n\
         fmt.println(a, b, c, d, e)\n }\n",
    );
    // Ordinary map-bearing structs and map-free parenthesized types are unchanged.
    assert!(go.contains("a := &bag{items: map[string]int{}}"), ":\n{go}");
    assert!(go.contains("b := new(plain)"), ":\n{go}");
    assert!(go.contains("c := new(int)"), ":\n{go}");
    assert!(
        !go.contains("&counts{"),
        "named/spread argument rewritten:\n{go}"
    );
}
