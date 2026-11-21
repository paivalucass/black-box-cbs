"""Serial, persistent Cmdenv jobs. No simulation process is launched by the browser."""
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from .compiler import compile_scenario
from .schema import Scenario
from .metrics import extract
from .occupancy import process_link_occupancy
from .results import PIPELINE_VERSION, export_command

ROOT=Path(__file__).resolve().parents[2]
WORKSPACE=ROOT.parent
DATA=Path(os.environ.get('TSN_DATA_DIR',ROOT/'var')).resolve()
DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'designer.sqlite3'

def connect():
    db=sqlite3.connect(DB); db.row_factory=sqlite3.Row
    return db

def init(recover=False):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, created REAL, body TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL, status TEXT, error TEXT, body TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS simulation_models (id TEXT PRIMARY KEY, name TEXT, name_key TEXT UNIQUE, created REAL, updated REAL, body TEXT)')
        # Import legacy revisions without deleting them or collapsing equal names.
        for old in db.execute('SELECT * FROM scenarios ORDER BY created').fetchall():
            if db.execute('SELECT id FROM simulation_models WHERE id=?',(old['id'],)).fetchone(): continue
            body=json.loads(old['body']); base=body.get('name','Untitled simulation').strip() or 'Untitled simulation'
            name=base; suffix=2
            while db.execute('SELECT id FROM simulation_models WHERE name_key=?',(name.casefold(),)).fetchone():
                name=f'{base} ({suffix})'; suffix+=1
            body['name']=name
            db.execute('INSERT INTO simulation_models VALUES (?,?,?,?,?,?)',(old['id'],name,name.casefold(),old['created'],old['created'],json.dumps(body)))
        if recover: db.execute("UPDATE jobs SET status='interrupted',error='Backend stopped before completion' WHERE status IN ('running','queued','validating','exporting','extracting')")

def list_jobs():
    with connect() as db: rows=db.execute('SELECT id,created,status,error,body FROM jobs ORDER BY created DESC').fetchall()
    result=[]
    for row in rows:
        r=dict(row); r['name']=json.loads(r.pop('body'))['scenario']['name']; result.append(r)
    return result

def job(job_id):
    with connect() as db: row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if row is None: raise KeyError(job_id)
    return dict(row)

def update(job_id,status,error=None):
    with connect() as db: db.execute('UPDATE jobs SET status=?,error=? WHERE id=?',(status,error,job_id))

def environment_command(script):
    if os.environ.get('OPP_ENV_VERSION') and os.environ.get('OMNETPP_ROOT'):
        return ['bash','-c',script]
    return ['opp_env','run','inet-4.7.0','--no-build','-c',script]

def simulation_script(bundle):
    inet=Path(os.environ.get('INET_ROOT',WORKSPACE/'inet-4.7.0'))
    omnet=Path(os.environ.get('OMNETPP_ROOT',WORKSPACE/'omnetpp-6.4.0'))
    excludes=';'.join((inet/'.nedexclusions').read_text().splitlines())
    args=[str(omnet/'bin/opp_run'),'-u','Cmdenv','-l',str(inet/'src/INET'),'-n',f'{inet}/src:{bundle}/ned','-f',str(bundle/'omnetpp.ini'),'--result-dir='+str(bundle/'results')]
    if excludes: args+=['-x',excludes]
    return shlex.join(args)

_lock=threading.Lock(); _processes={}; _cancelled=set()

def cancel(job_id):
    row=job(job_id)
    if row['status'] not in ['queued','validating','running','exporting','extracting']: return
    _cancelled.add(job_id)
    proc=_processes.get(job_id)
    if proc and proc.poll() is None:
        try: os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        def kill():
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
        threading.Thread(target=kill,daemon=True).start()
    update(job_id,'cancelled')

def submit(s,candidates=None):
    result=compile_scenario(s,candidates)
    id=uuid.uuid4().hex; bundle=DATA/'runs'/id; bundle.mkdir(parents=True)
    for name,text in result['files'].items():
        dest=bundle/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(text+'\n' if not text.endswith('\n') else text)
    script=simulation_script(bundle)
    (bundle/'command.txt').write_text(shlex.join(environment_command(script))+'\n')
    body=dict(scenario=json.loads(result['files']['scenario.json']),manifest=result['manifest'])
    with connect() as db: db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(id,time.time(),'queued',None,json.dumps(body)))
    threading.Thread(target=work,args=(id,bundle,body),daemon=True).start()
    return id

def write_json(path, data):
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,indent=2,allow_nan=False))
    tmp.replace(path)


def pipeline_step(bundle, name, status, **details):
    path=bundle/'pipeline.json'
    pipeline=json.loads(path.read_text()) if path.exists() else dict(version=PIPELINE_VERSION,stages={})
    pipeline['version']=PIPELINE_VERSION
    pipeline['stages'][name]=dict(status=status,**details)
    write_json(path,pipeline)


def native_outputs(bundle):
    return sorted((bundle/'results').glob('*.vec'))+sorted((bundle/'results').glob('*.sca'))


def simulation_finished(bundle):
    log=bundle/'run.log'
    return log.exists() and 'Simulation time limit reached' in log.read_text(errors='replace')


def can_extract(id):
    row=job(id); bundle=DATA/'runs'/id
    return row['status'] in ['failed','interrupted'] and simulation_finished(bundle) and bool(
        native_outputs(bundle) or (bundle/'native-results.json').exists() or (bundle/'raw.csv').exists())


def execute(id, bundle, command, log_name):
    if id in _cancelled: raise RuntimeError('Cancelled')
    with (bundle/log_name).open('a') as out:
        out.write('$ '+shlex.join(command)+'\n'); out.flush()
        proc=subprocess.Popen(command,cwd=WORKSPACE,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        _processes[id]=proc
        if id in _cancelled: cancel(id)
        try:
            code=proc.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL); proc.wait()
            raise RuntimeError('Process exceeded two-hour wall time limit')
        finally: _processes.pop(id,None)
    if code: raise RuntimeError(f'Process exited with code {code}; see {log_name}')


def process_results(id,bundle,body,recovered=False,started=None):
    """The sole export/compute/publish path, used by new runs and recovery."""
    scenario=Scenario.model_validate(body['scenario'])
    inputs=native_outputs(bundle)
    exported=bundle/'native-results.json'
    if inputs:
        update(id,'exporting')
        pipeline_step(bundle,'export','running',format='JSON',inputs=[str(p.relative_to(bundle)) for p in inputs])
        omnet=Path(os.environ.get('OMNETPP_ROOT',WORKSPACE/'omnetpp-6.4.0'))
        tmp=bundle/'native-results.json.tmp'
        command=export_command(omnet/'bin/opp_scavetool',inputs,tmp,body['manifest'])
        (bundle/'export-command.txt').write_text(shlex.join(command)+'\n')
        # scavetool is a standalone result tool. No second opp_env shell or
        # simulation is needed to read the existing native output files.
        execute(id,bundle,command,'export.log')
        tmp.replace(exported)
        pipeline_step(bundle,'export','completed',format='JSON',output=exported.name)
    elif exported.exists():
        pipeline_step(bundle,'export','skipped',format='JSON',reason='Using existing JSON export')
    elif (bundle/'raw.csv').exists():
        exported=bundle/'raw.csv'
        pipeline_step(bundle,'export','skipped',format='legacy CSV-R',reason='Only legacy export remains')
    else:
        raise ValueError('No native or exported results are available')
    if id in _cancelled: return
    update(id,'extracting')
    pipeline_step(bundle,'metrics','running',input=exported.name)
    metrics=extract(exported,scenario,body['manifest'])
    metrics['link_occupancy']=process_link_occupancy(bundle,scenario,body['manifest'],exported)
    metrics['results_pipeline']=PIPELINE_VERSION
    metrics['results_source']=exported.name
    metrics['recovered_from_existing_outputs']=recovered
    if started is not None:
        metrics['recovery_wall_seconds' if recovered else 'wall_seconds']=time.monotonic()-started
    write_json(bundle/'metrics.json',metrics)
    pipeline_step(bundle,'metrics','completed',output='metrics.json')
    if id not in _cancelled: update(id,'completed')


def fail(id,bundle,exc):
    if id in _cancelled: return
    stage=job(id)['status']
    name={'running':'simulation','exporting':'export','extracting':'metrics'}.get(stage,stage)
    pipeline_step(bundle,name,'failed',error=str(exc))
    with (bundle/'processing.log').open('a') as log:
        log.write(f'{name}: {type(exc).__name__}: {exc}\n')
    update(id,'failed',f'{name.capitalize()} failed: {exc}')


def work(id,bundle,body):
    with _lock:
        if id in _cancelled:
            _cancelled.discard(id); return
        started=time.monotonic()
        try:
            update(id,'validating')
            Scenario.model_validate(body['scenario'])
            update(id,'running'); pipeline_step(bundle,'simulation','running')
            execute(id,bundle,environment_command(simulation_script(bundle)),'run.log')
            if id in _cancelled: return
            if not simulation_finished(bundle):
                raise RuntimeError('Simulation did not confirm its configured time horizon')
            pipeline_step(bundle,'simulation','completed')
            process_results(id,bundle,body,started=started)
        except Exception as exc: fail(id,bundle,exc)
        finally: _cancelled.discard(id)


def retry_extraction(id):
    """Re-export native files and compute metrics; never call the simulator."""
    row=job(id)
    if not can_extract(id):
        raise ValueError('Recovery requires a finished simulation and saved native/exported results')
    bundle=DATA/'runs'/id
    update(id,'queued')
    def recover():
        with _lock:
            if id in _cancelled:
                _cancelled.discard(id); return
            started=time.monotonic()
            try:
                pipeline_step(bundle,'simulation','completed',reused=True)
                process_results(id,bundle,json.loads(row['body']),recovered=True,started=started)
            except Exception as exc: fail(id,bundle,exc)
            finally: _cancelled.discard(id)
    threading.Thread(target=recover,daemon=True).start()
