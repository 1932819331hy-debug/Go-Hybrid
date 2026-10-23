# N5 read-produced zero values: bounded design recommendation

This is a design recommendation for #1206 Part 2, not an implementation or a
D11 decision. The original reporter, planners1490, already described B1–B5 and
helper-based approaches. Execution results belong in `RESULTS.md`.

## What the current source establishes

At base `7d63d5fc25ceb525c2d0f6ec89a4ad608a6a5011`, `zeroinit.rs` only builds
map initializers for explicit declarations, non-generic structs (recursively),
struct literals' omitted fields, and `new(T)`. Alias resolution and recursion
are depth-bounded at 16. `stmt.rs` invokes this for typed `var` without a value;
`expr.rs` invokes it for struct literal/new construction. `Expr::Index` and
`Expr::Receive` emit ordinary Go reads (`expr.rs:179–188`). Assignment uses
`emit_place` without an N5 guard. An expression-produced zero therefore never
passes through the declaration initializer table.

N3 requires field-wise struct zeros and empty map zeros; T8 preserves comma-ok
and length semantics; C3 requires exhausted closed receives to yield zero with
`ok == false`. N6 separately preserves nil-map write panics for Go-owned
storage. D11 remains open (`SPEC-LANGUAGE.md:2182`). O8 already supplies an
explicit map-slot loan with read/copy/store-back; ordinary reads are not loans.
These constraints make a blanket rewrite of every Go map write inappropriate.

## Three approaches

| Approach | B1 direct nested write | B2–B4 extracted reads | B5 closed receive | Main cost/risk |
|---|---|---|---|---|
| Eager normalization of produced zero values | A fresh local inner map alone loses the update; durable behavior needs slot insertion | Produce a fresh empty map / recursively initialized struct on a missing read; no outer insertion | Fresh normalized zero per exhausted receive; preserve false | Allocation happens at read; requires known recursive type and a boundary policy |
| Lazy write-site guard + slot writeback | Get-or-insert can persist the inner map | Guard can make the written local safe, but previous copies of a nil map stay nil | Guard makes this local safe, but previous copies have the same problem | Copy identity, non-addressable slots, effect order, provenance; wrapper representation needed for universal alias fidelity |
| Fail-closed admission | Reject unproven write/escape with an actionable diagnostic | Reject unproven read-produced map flow or materialize before escape | Reject unsafe flow or normalize exhausted receives | Conservative rejections; needs sound origin/flow tracking, not a syntactic pattern ban |

“Eager recursive” must mean normalization of a finite *value/type shape*. It
cannot preallocate all possible map keys. Recursing through a missing
`map[string]node` where `node` itself contains the same map type must stop at
an empty map; enumerate struct/array fields only, never potential map entries.
Depth exhaustion and unresolved/generic shapes must yield a diagnostic in an
admitted lowering, not silently return Go's unsafe zero.

## Identity and observable behavior

For present entries, preserve the existing map object. `a := mm[k]; b := a;
a[x] = 1` must remain visible through `b` and `mm[k]`. Struct copies likewise
share existing map fields. Never replace a non-nil map or rebuild present
struct fields unnecessarily.

For a missing entry there is no existing map object. Normalizing each read to
a fresh empty map gives `a := mm[k]; b := a` one shared object, but two
independent reads `a := mm[k]; b := mm[k]` two objects. The outer map remains
absent (`len` unchanged and future `ok` false). A write-only pointer helper
allocates after `b := a`; it updates `a`'s map header, not `b`'s header. That
is observably different from declaration-site N5 allocation and from normalized
read semantics. Pointer type inference alone does not repair this difference.

A get-or-insert read is different again: `len(mm)` grows and later comma-ok
becomes true merely from reading. Even if it returns the original false once,
it changes future membership. Do not use it for ordinary reads. Reserve any
implicit insertion for an explicitly approved direct nested *write* operation;
plain extracted values remain copies, with explicit `mm[k] = local` or O8
loan syntax required to update the outer slot.

Exhausted closed receives have no storage slot to write back into. Every
receive must preserve `ok == false` and produce an independent zero value;
copying one resulting value before writing must preserve that value's aliases.
Buffered values received after `close` still have `ok == true` and must keep
their existing map identity. Zero-channel blocking, empty-open-channel blocking,
send behavior, channel capacity, and `close` behavior must remain untouched.

Allocation is observable through work/memory and via aliases or membership;
this spike makes no performance claim. A helper with correct result bytes
could still have wrong allocation timing or mutate a map during a read.

## Recommended minimal implementation, conditional on a recorded decision

Prefer **normalization at read production for B2–B5, and a separately bounded
slot lowering for B1**, rather than a universal write guard. This keeps existing
Go map representation and avoids silently changing ordinary reads into loans.

1. Restrict the first patch to Hybrid-owned maps/channels, primitive map key
   types already admitted by the checker, direct map values and known
   non-generic structs with map fields. Resolve aliases structurally. Materialize
   a missing lookup's result once; normalize only that result, preserving the
   original boolean. For one-value lookup, lower internally to value/ok once.
   For closed receive, perform exactly one receive and normalize only when
   `ok` is false. Preserve buffered present values. Share the logic between
   single-value and comma-ok binding forms; do not compute `ok` from length.
2. If maintainers approve B1 insertion, lower only a direct single-target
   nested map write into a typed temporary/slot operation. Evaluate the outer
   expression, each key, and RHS once in the language's required order.
   Capture all required operand values before insertion can affect subsequent
   operand reads. Allocate the absent inner map and store it into the outer
   map once; then perform the intended write. The minimal support set should
   initially reject compound, increment, parallel, deeper nested, side-effectful,
   or unsupported place forms unless their complete evaluation order is proven.
   A naive `hy_n5_slot(mm, key)[key2] = rhs` may insert before evaluating
   `key2`/`rhs`; exactly-once alone is insufficient.
3. Use an explicit admission result (supported initializer / Go-zero already
   safe / unsupported) instead of overloading `None`. For unsupported known
   N5-producing values, issue an existing appropriate unsupported-emission
   diagnostic before producing unsafe Go. Do not infer Hybrid ownership from
   map type alone; retain N6 behavior. A sound provenance policy or a deliberately
   narrower admitted source surface is required before covering interprocedural
   or imported values.
4. Add compiler acceptance/output tests for all B1–B5, this spike's controls,
   aliases copied before first write, two independent missing reads, buffered
   channel values followed by exhausted reads, nested struct maps, aliases,
   exactly-once/order counters and membership-sensitive RHS, assignment versus
   declaration, and package emission. Add diagnostic tests for every excluded
   shape. Run the repository gates for that future compiler patch. This research
   branch does not claim those future cases or changes are already implemented.

A pure fail-closed interim patch is acceptable only if its rejection boundary
is sound and explicitly documented as temporary implementation incompleteness.
A warning that still emits the panic does not meet the issue's fail-closed
alternative. A local pattern matching `m := mm[k]; m[x] = y` is insufficient:
aliases, assignments, branches, returns, calls, and closures can carry the zero.
Prefer rejecting unsupported producers/escapes conservatively to promising
flow coverage that has not been implemented.

## Maintainer decision needed before compiler work

Record whether (a) plain missing reads produce independent usable zero maps
without inserting, (b) copying one produced zero preserves map identity,
(c) direct nested writes implicitly insert their missing inner map, and
(d) exhausted closed receives yield independent usable zero values. The
recommendation is yes to all four, with insertion limited to direct writes.
These are concrete observable rules beyond saying “do not panic”; D11 and
N6 ownership boundaries must be reconciled explicitly. If maintainers choose
Go-exact zeros instead, N3/N5 and guarantees need a logged amendment and tests;
this spike neither makes nor authorizes that change.

Part 1 (#1207/#1208) initializes allocation-site values. It cannot supply these
read-site identity or insertion rules. Their snapshots are context, not evidence
that either PR passed CI or that its performance/credit assertions are correct.
