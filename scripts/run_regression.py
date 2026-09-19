"""Run maintained tests in isolated processes, including Qt/event-loop tests."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time
from xml.etree import ElementTree as ET


def merge_reports(out, rows):
    """Write one report for this run, never include stale files from older runs."""
    report = ET.Element('testsuites', name='maintained-tests')
    totals = dict(tests=0, failures=0, errors=0, skipped=0)
    for row in rows:
        path = out / (Path(row['file']).stem + '.xml')
        if path.exists():
            for suite in ET.parse(path).iter('testsuite'):
                report.append(suite)
                for key in totals:
                    totals[key] += int(suite.get(key, 0))
        else:
            suite = ET.SubElement(report, 'testsuite', name=row['file'],
                                  tests='1', errors='1', failures='0', skipped='0')
            case = ET.SubElement(suite, 'testcase', name='test_process_completed')
            ET.SubElement(case, 'error', message=f"process returned {row['exit_code']}").text = (
                'No JUnit report was produced; inspect the corresponding log.')
            totals['tests'] += 1
            totals['errors'] += 1
    for key, value in totals.items():
        report.set(key, str(value))
    report.set('time', str(round(sum(row['seconds'] for row in rows), 2)))
    ET.ElementTree(report).write(out/'full_regression.xml', encoding='utf-8', xml_declaration=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='build/regression')
    parser.add_argument('--timeout',type=int,default=180,help='seconds per test file')
    parser.add_argument('--files',nargs='*',help='test filenames inside tests/, default all')
    args=parser.parse_args()
    if args.timeout<1:parser.error('--timeout must be positive')
    root=Path(__file__).resolve().parents[1]
    known={p.name:p for p in (root/'tests').glob('test*.py')}
    names=args.files or sorted(known)
    if any(name not in known for name in names):parser.error('--files must name files inside tests/')
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.update(QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    rows=[];flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    for name in names:
        started=time.monotonic();stem=known[name].stem
        xml=out/(stem+'.xml');log=out/(stem+'.log')
        # Avoid treating an older successful XML as a timed-out run's report.
        if xml.exists():xml.unlink()
        print('START',name,flush=True)
        with log.open('w',encoding='utf-8') as stream:
            process=subprocess.Popen([sys.executable,'-m','pytest',str(known[name]),'-q',
                f'--junitxml={xml}','--tb=short','-o',f'faulthandler_timeout={max(1,args.timeout-10)}'],
                cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT,creationflags=flags)
            try:status=process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=stream,stderr=stream,creationflags=flags)
                else:process.kill()
                process.wait(timeout=10);status='timeout'
        row=dict(file=name,exit_code=status,seconds=round(time.monotonic()-started,2),tests=0,failures=0,errors=0,skipped=0)
        if xml.exists():
            for suite in ET.parse(xml).iter('testsuite'):
                for key in ('tests','failures','errors','skipped'):row[key]+=int(suite.get(key,0))
        rows.append(row)
        (out/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        merge_reports(out, rows)
        print(json.dumps(row),flush=True)
    return int(any(r['exit_code']!=0 or r['failures'] or r['errors'] for r in rows))

if __name__=='__main__':sys.exit(main())
