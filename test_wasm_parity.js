const fs = require('fs');
const path = require('path');

async function main() {
    const wasmPath = path.join(__dirname, 'compiler/target/wasm32-unknown-unknown/release/hy_wasm.wasm');
    const wasmBytes = fs.readFileSync(wasmPath);

    console.log(`Loaded WASM: ${wasmBytes.length} bytes (${(wasmBytes.length / 1024).toFixed(1)} KB)`);

    const tInit0 = performance.now();
    const wasmModule = await WebAssembly.instantiate(wasmBytes, {});
    const tInit1 = performance.now();
    console.log(`WASM instantiation time: ${(tInit1 - tInit0).toFixed(2)} ms`);

    const { hy_wasm_alloc, hy_wasm_free, hy_wasm_check, memory } = wasmModule.instance.exports;

    function checkSource(srcText) {
        const encoder = new TextEncoder();
        const encoded = encoder.encode(srcText);

        const srcPtr = hy_wasm_alloc(encoded.length);
        const memU8 = new Uint8Array(memory.buffer);
        memU8.set(encoded, srcPtr);

        const packed = BigInt(hy_wasm_check(srcPtr, encoded.length));
        hy_wasm_free(srcPtr, encoded.length);

        const outPtr = Number(packed >> 32n);
        const outLen = Number(packed & 0xFFFFFFFFn);

        const outSlice = new Uint8Array(memory.buffer, outPtr, outLen);
        const decoder = new TextDecoder();
        const jsonStr = decoder.decode(outSlice);

        return JSON.parse(jsonStr);
    }

    // Test a sample accept file
    const sampleAccept = fs.readFileSync(path.join(__dirname, 'tests/checker-corpus/16-move-send-clean.hy'), 'utf8');
    const resAccept = checkSource(sampleAccept);
    console.log('Sample Accept verdict:', resAccept.verdict, 'errors:', resAccept.errors);

    // Test a sample reject file
    const sampleReject = fs.readFileSync(path.join(__dirname, 'tests/checker-corpus/01-shared-map-across-tasks.hy'), 'utf8');
    const resReject = checkSource(sampleReject);
    console.log('Sample Reject verdict:', resReject.verdict, 'errors:', resReject.errors, 'rule:', resReject.diagnostics[0]?.tier0_rule);

    // Test across all 55 corpus files and measure time
    const corpusDir = path.join(__dirname, 'tests/checker-corpus');
    const files = fs.readdirSync(corpusDir).filter(f => f.endsWith('.hy')).sort();

    console.log(`\nVerifying all ${files.length} corpus files against WASM...`);
    let passed = 0;
    const times = [];

    for (const f of files) {
        const fullPath = path.join(corpusDir, f);
        const src = fs.readFileSync(fullPath, 'utf8');
        const m = src.match(/^\/\/\s*EXPECTED-VERDICT:\s*(\S+)\b\s*(\S*)/m);
        const expectedVerdict = m ? m[1] : null;
        const expectedRule = (m && m[2]) ? m[2] : null;

        const t0 = performance.now();
        const res = checkSource(src);
        const t1 = performance.now();
        times.push(t1 - t0);

        const verdict = res.summary ? res.summary.verdict : res.verdict;
        const errors = res.summary ? res.summary.errors : res.errors;

        let match = false;
        if (expectedVerdict === 'ACCEPT') {
            match = verdict === 'accept' && errors === 0;
        } else if (expectedVerdict === 'ACCEPT-MANAGED') {
            match = verdict === 'accept' && errors === 0;
        } else if (expectedVerdict === 'REJECT') {
            const firstErr = res.diagnostics.find(d => d.severity === 'error');
            const ruleOk = expectedRule ? (firstErr && firstErr.tier0_rule === expectedRule) : true;
            match = verdict === 'reject' && ruleOk;
        }

        if (match) {
            passed++;
        } else {
            console.error(`FAIL ${f}: expected ${expectedVerdict} (${expectedRule}), got ${res.verdict}`);
        }
    }

    times.sort((a, b) => a - b);
    const mean = times.reduce((a, b) => a + b, 0) / times.length;
    const p50 = times[Math.floor(times.length / 2)];
    const p90 = times[Math.floor(times.length * 0.9)];
    const p99 = times[times.length - 1];

    console.log(`Corpus parity: ${passed}/${files.length} (${(passed/files.length*100).toFixed(1)}%)`);
    console.log(`WASM Check Latency per file:`);
    console.log(`  mean: ${mean.toFixed(3)} ms`);
    console.log(`  p50:  ${p50.toFixed(3)} ms`);
    console.log(`  p90:  ${p90.toFixed(3)} ms`);
    console.log(`  p99:  ${p99.toFixed(3)} ms`);
}

main().catch(err => {
    console.error(err);
    process.exit(1);
});
