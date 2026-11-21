"""Loopback-only HTTP service for the Vue editor and shared evaluator."""
import io
import os
import json
import time
import uuid
import zipfile
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from pydantic import ValidationError
from .schema import Scenario
from .compiler import compile_scenario, InvalidScenario, COMPILER_VERSION
from . import runner, storage
from .routing import RouteQuery, route_options
from .occupancy import occupancy_window

app=Flask(__name__,static_folder=None)
app.config['MAX_CONTENT_LENGTH']=4*1024*1024
runner.init(recover=True)
BACKEND_STARTED_AT=time.time()

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

@app.get('/api/health')
def health():
    return jsonify(compiler_version=COMPILER_VERSION,results_pipeline=runner.PIPELINE_VERSION,started_at=BACKEND_STARTED_AT,pid=os.getpid())

@app.get('/api/templates')
def templates():
    return jsonify([Scenario.model_validate_json(p.read_text()).model_dump() for p in sorted((runner.ROOT/'scenarios').glob('*.json'))])

@app.get('/api/capabilities')
def capabilities():
    return jsonify(runtime='Cmdenv',features={'vlan_tagged':True,'pcp':True,'cbs':True,'strict_priority':True,'ingress_vlan_admission':True,'ingress_priority_buffers':False,'qci':False,'tas':False,'ats':False,'gptp':False,'source_clock_drift':True,'burst_generation':True,'traffic_class_queue_mapping':True,'multicast':True,'cyclic_physical_topologies':True,'automatic_ports':True,'cable_length_delay':True,'per_link_packet_timeline':True,'route_discovery':True,'explicit_routes':True,'node_clocks':True,'native_flow_measurements':True,'frer':False,'preemption':False,'cutthrough':False},profile='static routed tagged Ethernet applications',compiler_version=COMPILER_VERSION)

@app.post('/api/compile')
def compile_api(): return jsonify(compile_scenario(Scenario.model_validate(request.json)))

@app.post('/api/routes')
def routes_api():
    return jsonify(route_options(RouteQuery.model_validate(request.json)))

@app.post('/api/export')
def export():
    result=compile_scenario(Scenario.model_validate(request.json))
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for name,text in result['files'].items(): z.writestr(name,text)
    buffer.seek(0)
    return send_file(buffer,mimetype='application/zip',as_attachment=True,download_name='tsn-scenario.zip')

@app.errorhandler(storage.NameConflict)
def name_conflict(exc): return jsonify(error=str(exc)),409

@app.route('/api/scenarios',methods=['GET','POST'])
def scenarios():
    if request.method=='GET': return jsonify(storage.list_models())
    # Drafts may be saved before semantic simulation validation succeeds.
    return jsonify(storage.save_model(Scenario.model_validate(request.json))),201

@app.post('/api/scenarios/validate')
def validate_draft():
    return jsonify(Scenario.model_validate(request.json).model_dump())

@app.route('/api/scenarios/<id>',methods=['GET','PUT'])
def simulation(id):
    if request.method=='GET': return jsonify(storage.get_model(id))
    return jsonify(storage.save_model(Scenario.model_validate(request.json),id))

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
    result['can_extract']=runner.can_extract(id)
    result['export_log']=(bundle/'export.log').read_text(errors='replace')[-50000:] if (bundle/'export.log').exists() else ''
    result['processing_log']=(bundle/'processing.log').read_text(errors='replace')[-10000:] if (bundle/'processing.log').exists() else ''
    result['pipeline']=json.loads((bundle/'pipeline.json').read_text()) if (bundle/'pipeline.json').exists() else None
    result['metrics']=json.loads((bundle/'metrics.json').read_text()) if result['status']=='completed' else None
    return jsonify(result)

@app.get('/api/jobs/<id>/occupancy')
def link_occupancy(id):
    row=runner.job(id)
    if row['status']!='completed':raise ValueError('Statistics require a completed run')
    bundle=runner.DATA/'runs'/id
    metadata=json.loads((bundle/'metrics.json').read_text()).get('link_occupancy')
    if not metadata or not metadata.get('available'):raise ValueError((metadata or {}).get('reason','This run has no per-link packet timing. Launch a new simulation.'))
    return jsonify(occupancy_window(bundle,metadata,request.args.get('link'),request.args.get('start',0),request.args.get('end',metadata['observation_end']),int(request.args.get('limit',5000))))

@app.post('/api/jobs/<id>/cancel')
def cancel(id): runner.cancel(id); return jsonify(status='cancelled')

@app.post('/api/jobs/<id>/extract')
def retry_extraction(id):
    runner.retry_extraction(id)
    return jsonify(id=id,status='queued'),202

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

if __name__=='__main__':
    print(f'TSN backend PID {os.getpid()} — Ethernet compiler {COMPILER_VERSION} — results engine {runner.PIPELINE_VERSION}',flush=True)
    app.run(host='127.0.0.1',port=8000,threaded=True,use_reloader=False)
