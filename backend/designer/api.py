"""Loopback-only HTTP service for the Vue editor and shared evaluator."""
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from pydantic import ValidationError
from .schema import Scenario
from .compiler import compile_scenario, InvalidScenario
from . import runner

app=Flask(__name__,static_folder=None)
app.config['MAX_CONTENT_LENGTH']=4*1024*1024
runner.init(recover=True)

@app.before_request
def local_only():
    # Prevent cross-site requests to the local subprocess-control service.
    if request.host.split(':')[0] not in ['127.0.0.1','localhost','[::1]']: return jsonify(error='Local access only'),403
    origin=request.headers.get('Origin')
    if origin and origin not in ['http://'+request.host, 'http://localhost:5173','http://127.0.0.1:5173']:
        return jsonify(error='Origin rejected'),403

@app.errorhandler(ValidationError)
def schema_error(exc): return jsonify(error='Invalid scenario',diagnostics=json.loads(exc.json())),422

@app.errorhandler(InvalidScenario)
def semantic_error(exc): return jsonify(error='Invalid scenario',diagnostics=exc.diagnostics),422

@app.errorhandler(ValueError)
def value_error(exc): return jsonify(error=str(exc)),422

@app.errorhandler(KeyError)
def not_found(exc): return jsonify(error='Not found'),404

@app.get('/api/templates')
def templates():
    return jsonify([json.loads(p.read_text()) for p in sorted((runner.ROOT/'scenarios').glob('*.json'))])

@app.get('/api/capabilities')
def capabilities():
    return jsonify(runtime='Cmdenv',features={'vlan_tagged':True,'pcp':True,'cbs':True,'strict_priority':True,'ingress_vlan_admission':True,'ingress_priority_buffers':False,'qci':False,'tas':False,'ats':False,'gptp':False,'frer':False,'preemption':False,'cutthrough':False},profile='acyclic tagged periodic IPv4/UDP')

@app.post('/api/compile')
def compile_api(): return jsonify(compile_scenario(Scenario.model_validate(request.json)))

@app.post('/api/export')
def export():
    result=compile_scenario(Scenario.model_validate(request.json))
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for name,text in result['files'].items(): z.writestr(name,text)
    buffer.seek(0)
    return send_file(buffer,mimetype='application/zip',as_attachment=True,download_name='tsn-scenario.zip')

@app.route('/api/scenarios',methods=['GET','POST'])
def scenarios():
    with runner.connect() as db:
        if request.method=='GET': return jsonify([dict(r) for r in db.execute('SELECT * FROM scenarios ORDER BY created DESC')])
        s=Scenario.model_validate(request.json); compile_scenario(s)
        id=uuid.uuid4().hex
        db.execute('INSERT INTO scenarios VALUES (?,?,?)',(id,time.time(),s.model_dump_json()))
    return jsonify(id=id),201

@app.route('/api/jobs',methods=['GET','POST'])
def jobs():
    if request.method=='GET': return jsonify(runner.list_jobs())
    data=request.json
    id=runner.submit(Scenario.model_validate(data['scenario']),data.get('candidates'))
    return jsonify(id=id),202

@app.get('/api/jobs/<id>')
def job(id):
    result=runner.job(id); result['body']=json.loads(result['body'])
    bundle=runner.DATA/'runs'/id
    result['log']=(bundle/'run.log').read_text(errors='replace')[-100000:] if (bundle/'run.log').exists() else ''
    result['command']=(bundle/'command.txt').read_text()
    result['metrics']=json.loads((bundle/'metrics.json').read_text()) if result['status']=='completed' else None
    return jsonify(result)

@app.post('/api/jobs/<id>/cancel')
def cancel(id): runner.cancel(id); return jsonify(status='cancelled')

@app.get('/api/jobs/<id>/events')
def events(id):
    runner.job(id)
    def stream():
        for _ in range(7200):
            row=runner.job(id)
            yield 'data: '+json.dumps({k:row[k] for k in ['id','status','error']})+'\n\n'
            if row['status'] in ['completed','failed','cancelled','interrupted']: return
            time.sleep(1)
    return Response(stream(),mimetype='text/event-stream')

@app.get('/api/jobs/<id>/artifacts')
def artifacts(id):
    runner.job(id); buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for p in (runner.DATA/'runs'/id).rglob('*'):
            if p.is_file(): z.write(p,str(p.relative_to(runner.DATA/'runs'/id)))
    buffer.seek(0)
    return send_file(buffer,mimetype='application/zip',download_name=f'run-{id}.zip',as_attachment=True)

@app.get('/')
@app.get('/<path:path>')
def frontend(path='index.html'):
    dist=runner.ROOT/'web/dist'
    if not (dist/'index.html').exists(): return jsonify(error='Frontend not built. Run npm install and npm run build in web/.'),503
    return send_from_directory(dist,path)

if __name__=='__main__': app.run(host='127.0.0.1',port=8000,threaded=True,use_reloader=False)
