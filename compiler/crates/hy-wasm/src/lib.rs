use std::slice;
use std::str;

#[no_mangle]
pub extern "C" fn hy_wasm_alloc(len: usize) -> *mut u8 {
    let mut buf = Vec::with_capacity(len);
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    ptr
}

#[no_mangle]
pub unsafe extern "C" fn hy_wasm_free(ptr: *mut u8, len: usize) {
    if !ptr.is_null() {
        let _ = Vec::from_raw_parts(ptr, len, len);
    }
}

/// Takes source code, checks it, returns a pointer to a JSON envelope in memory.
/// Returns (ptr, len) packed as u64.
#[no_mangle]
pub unsafe extern "C" fn hy_wasm_check(src_ptr: *const u8, src_len: usize) -> u64 {
    let slice = slice::from_raw_parts(src_ptr, src_len);
    let src = match str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return 0,
    };

    let (diags, summary) = match hy_syntax::parse_file("input.hy", src) {
        Ok(ast) => {
            let res = hy_check::check_file(&ast);
            let errors = res
                .diagnostics
                .iter()
                .filter(|d| d.severity == hy_diag::Severity::Error)
                .count();
            let warnings = res
                .diagnostics
                .iter()
                .filter(|d| d.severity == hy_diag::Severity::Warning)
                .count();
            let summary = hy_diag::CheckSummary {
                file: "input.hy".into(),
                verdict: res.verdict.as_str().to_string(),
                errors,
                warnings,
                managed: None,
            };
            (res.diagnostics, summary)
        }
        Err(diags) => {
            let summary = hy_diag::CheckSummary {
                file: "input.hy".into(),
                verdict: "reject".into(),
                errors: diags.len(),
                warnings: 0,
                managed: None,
            };
            (diags, summary)
        }
    };

    let json = hy_diag::check_output_json(&diags, &summary);
    let bytes = json.into_bytes();
    let len = bytes.len();
    let ptr = bytes.as_ptr() as u32;
    std::mem::forget(bytes);

    ((ptr as u64) << 32) | (len as u64)
}
