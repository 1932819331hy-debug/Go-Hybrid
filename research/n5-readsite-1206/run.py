#!/usr/bin/env python3
"""Bounded characterization; raw failures are data, not passing language behavior."""
import hashlib, json, os, shutil, subprocess, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = '7d63d5fc25ceb525c2d0f6ec89a4ad608a6a5011'
OUT = HERE / 'results'
def capture(argv, cwd, seconds):
    start = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=seconds)
        return dict(argv=list(map(str,argv)), rc=p.returncode, stdout=p.stdout, stderr=p.stderr, seconds=round(time.monotonic()-start,3))
    except subprocess.TimeoutExpired as e:
        return dict(argv=list(map(str,argv)), rc=124, stdout=str(e.stdout or ''), stderr=str(e.stderr or ''), seconds=seconds)
def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def main():
    assert not os.environ.get('HYB_ALLOW_TOOLCHAIN_DRIFT'), 'toolchain drift bypass prohibited'
    assert git('rev-parse','HEAD:compiler') == git('rev-parse',BASE+':compiler'), 'compiler differs from base'
    assert not git('status','--porcelain','--','compiler'), 'dirty compiler'
    hyb = ROOT/'compiler/target/debug/hyb'
    assert hyb.is_file()
    OUT.mkdir(exist_ok=True)
    meta = dict(head=git('rev-parse','HEAD'),base=BASE,compiler_tree=git('rev-parse','HEAD:compiler'),hyb=str(hyb),sha256=hashlib.sha256(hyb.read_bytes()).hexdigest(),go=capture(['go','version'],ROOT,10),rust=capture(['rustc','--version'],ROOT,10))
    assert meta['go']['stdout'].startswith('go version go1.26.9 ')
    assert meta['rust']['stdout'].startswith('rustc 1.99.0 ')
    (OUT/'provenance.json').write_text(json.dumps(meta,indent=2)+'\n')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='n5-readsite-') as tmp:
        for case in json.loads((HERE/'cases.json').read_text()):
            name=case['name']; work=Path(tmp)/name; work.mkdir()
            src=work/'main.hy'; shutil.copyfile(HERE/'fixtures'/f'{name}.hy',src)
            logs=OUT/name; logs.mkdir(exist_ok=True)
            check=capture([str(hyb),'check',str(src)],work,30)
            build=capture([str(hyb),'build',str(src),'-o',str(work/'prog')],work,120)
            run=capture([str(work/'prog')],work,10) if build['rc']==0 else None
            for stage,data in [('check',check),('build',build),('run',run)]:
                (logs/f'{stage}.json').write_text(json.dumps(data,indent=2)+'\n')
            for emitted in work.glob('*.go'): shutil.copyfile(emitted,logs/emitted.name)
            matched=check['rc']==build['rc']==0 and run is not None and ((case['expected']=='panic' and run['rc'] not in (0,124) and 'assignment to entry in nil map' in run['stderr']) or (case['expected']=='ok' and run['rc']==0 and run['stdout']==case['stdout']))
            row=dict(name=name,check=check['rc'],build=build['rc'],run=run['rc'] if run else None,prediction_matched=bool(matched))
            rows.append(row); print(json.dumps(row),flush=True)
    (OUT/'matrix.json').write_text(json.dumps(rows,indent=2)+'\n')
    return 0 if all(r['prediction_matched'] for r in rows) else 1
if __name__=='__main__': raise SystemExit(main())
