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
        if recover: db.execute("UPDATE jobs SET status='interrupted',error='Backend stopped before completion' WHERE status IN ('running','queued','validating','extracting')")

def list_jobs():
    with connect() as db: return [dict(r) for r in db.execute('SELECT id,created,status,error FROM jobs ORDER BY created DESC')]

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
    if row['status'] not in ['queued','validating','running','extracting']: return
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

def work(id,bundle,body):
    with _lock:
        if id in _cancelled: return
        started=time.monotonic()
        try:
            update(id,'validating')
            scenario=Scenario.model_validate(body['scenario'])
            log=bundle/'run.log'
            def execute(script):
                if id in _cancelled: raise RuntimeError('Cancelled')
                with log.open('a') as out:
                    proc=subprocess.Popen(environment_command(script),cwd=WORKSPACE,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                    _processes[id]=proc
                    if id in _cancelled: cancel(id)
                    try: code=proc.wait(timeout=7200)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL); proc.wait(); raise RuntimeError('Run exceeded two-hour wall time limit')
                    finally: _processes.pop(id,None)
                    if code: raise RuntimeError(f'Process exited with code {code}; inspect run.log for environment or model errors')
            update(id,'running'); execute(simulation_script(bundle))
            if id in _cancelled: return
            if 'Simulation time limit reached' not in log.read_text(errors='replace'):
                raise RuntimeError('Simulation did not confirm the configured time horizon; results are incomplete')
            update(id,'extracting')
            outputs=sorted((bundle/'results').glob('*.vec'))+sorted((bundle/'results').glob('*.sca'))
            if not outputs: raise RuntimeError('Simulation produced no result files')
            omnet=Path(os.environ.get('OMNETPP_ROOT',WORKSPACE/'omnetpp-6.4.0'))
            args=[str(omnet/'bin/opp_scavetool'),'export','-F','CSV-R','-o',str(bundle/'raw.csv'),*map(str,outputs)]
            execute(shlex.join(args))
            metrics=extract(bundle/'raw.csv',scenario,body['manifest']); metrics['wall_seconds']=time.monotonic()-started
            (bundle/'metrics.json').write_text(json.dumps(metrics,indent=2,allow_nan=False))
            if id not in _cancelled: update(id,'completed')
        except Exception as exc:
            if id not in _cancelled: update(id,'failed',str(exc))
        finally: _cancelled.discard(id)
