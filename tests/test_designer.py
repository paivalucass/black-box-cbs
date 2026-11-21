import csv
import json
from pathlib import Path
import pytest
from designer.compiler import compile_scenario, InvalidScenario
from designer.schema import Scenario
from designer.metrics import extract

ROOT=Path(__file__).resolve().parents[1]
def scenario(): return Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())

def legacy_manifest(s):
    """Model the v1 result archives produced before sequence observations."""
    from collections import defaultdict
    apps=defaultdict(int);modules={}
    for f in sorted(s.flows,key=lambda f:f.id):
        src=apps[f.source];apps[f.source]+=1
        dest=f.receivers[0];sink=apps[dest];apps[dest]+=1
        modules[f.id]=dict(source=f'Scenario.{f.source}.app[{src}].source',sink=f'Scenario.{dest}.app[{sink}].sink')
    return dict(compiler_version='1.0',measurement_version=1,flow_modules=modules)


def test_templates_compile():
    for p in (ROOT/'scenarios').glob('*.json'):
        result=compile_scenario(Scenario.model_validate_json(p.read_text()))
        assert 'user-interface = Cmdenv' in result['files']['omnetpp.ini']
        assert 'ethg++' not in result['files'][f'ned/{Scenario.model_validate_json(p.read_text()).ned_name}.ned']
        assert all(paths for paths in result['paths'].values())
        assert 'PacketAtCollectionEndDropper' in result['files']['omnetpp.ini']

def test_layout_does_not_change_semantic_identity():
    s=scenario(); first=compile_scenario(s)
    s.nodes[0].x+=50
    assert first['manifest']['semantic_hash']==compile_scenario(s)['manifest']['semantic_hash']

def test_vlan_and_class_errors():
    s=scenario(); s.nodes[0].ports[0].vlans=[20]
    with pytest.raises(InvalidScenario,match='unreachable'): compile_scenario(s)
    s=scenario(); s.nodes[0].ports[0].pcp_map[4]=7
    with pytest.raises(InvalidScenario): compile_scenario(s)

def test_bad_endpoint_and_cycle_are_diagnostics():
    s=scenario(); s.links[0].a.node='missing'
    with pytest.raises(InvalidScenario): compile_scenario(s)
    s=scenario(); s.links.append(s.links[0].model_copy(update={'id':'duplicateCable'}))
    with pytest.raises(InvalidScenario): compile_scenario(s)

def test_candidates_only_change_selected_cbs():
    s=scenario(); key='sw0/2/1'
    base=compile_scenario(s); changed=compile_scenario(s,{key:15000000})
    assert s.nodes[-1].ports[2].queues[1].idle_slope==20000000
    assert base['manifest']['semantic_hash']!=changed['manifest']['semantic_hash']
    with pytest.raises(ValueError): compile_scenario(s,{'talker/0/0':12})
    with pytest.raises(InvalidScenario): compile_scenario(s,{key:100000001})


def test_deadlines_need_not_mature_before_simulation_end():
    s=scenario(); s.experiment.duration=1
    for f in s.flows:f.deadline=2
    compile_scenario(s)


def test_cohort_metrics_missing_deadlines_duplicates(tmp_path):
    s=scenario(); s.flows=s.flows[:1]; s.experiment.warmup=.001; f=s.flows[0]; f.legacy_stop=.004; f.deadline=.001
    m=legacy_manifest(s); mods=m['flow_modules']['control']
    output=tmp_path/'raw.csv'
    with output.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['type','module','name','vectime','vecvalue','value']); writer.writeheader()
        writer.writerow(dict(type='vector',module=mods['source'],name='packetLengths:vector',vectime='0 .001 .002 .003',vecvalue='1000 1000 1000 1000'))
        # pre-warm-up packet arrives after warm-up and must not enter the cohort.
        writer.writerow(dict(type='vector',module=mods['sink'],name='packetLifeTime:vector',vectime='.0011 .0012 .0035 .0036',vecvalue='.0011 .0002 .0015 .0016'))
    result=extract(output,s,m)['flows']['control']
    assert result['generated']==3 and result['received']==2
    assert result['missing_at_horizon']==1 and result['duplicates']==1
    assert result['deadline_misses']==2
    assert result['latency_seconds']['mean']==pytest.approx(.00085)
    assert result['mean_absolute_ipdv_seconds']==pytest.approx(.0013)


def test_no_reception_has_unavailable_latency(tmp_path):
    s=scenario(); s.flows=s.flows[:1]; m=legacy_manifest(s); src=m['flow_modules']['control']['source']
    p=tmp_path/'r.csv'; p.write_text(f'type,module,name,vectime,vecvalue,value\nvector,{src},packetLengths:vector,0,1000,\nscalar,{m["flow_modules"]["control"]["sink"]},packets:count,,,0\n')
    r=extract(p,s,m)['flows']['control']
    assert r['latency_seconds']['mean'] is None and r['deadline_misses']==1


def test_api_compile_export_persistence_and_origin(tmp_path,monkeypatch):
    monkeypatch.setenv('TSN_DATA_DIR',str(tmp_path))
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'test.sqlite')
    from designer.api import app
    runner.init(); client=app.test_client()
    s=scenario().model_dump()
    assert client.post('/api/compile',json=s).status_code==200
    assert client.post('/api/scenarios',json=s).status_code==201
    assert len(client.get('/api/scenarios').json)==1
    assert client.post('/api/export',json=s).data[:2]==b'PK'
    assert client.post('/api/compile',json=s,headers={'Origin':'https://outside.example'}).status_code==403
    s['links'][0]['a']['node']='missing'
    assert client.post('/api/compile',json=s).status_code==422


def test_scavetool_export_matches_reader(tmp_path):
    """Use the installed official exporter, independently of simulator launch."""
    import subprocess
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists(): pytest.skip('OMNeT++ result tool unavailable')
    s=scenario(); s.flows=s.flows[:1]; m=legacy_manifest(s); mods=m['flow_modules']['control']
    vec=tmp_path/'measure.vec'
    vec.write_text('version 2\nrun test\n'
        f'vector 0 "{mods["source"]}" "packetLengths:vector" TV\n'
        f'vector 1 "{mods["sink"]}" "packetLifeTime:vector" TV\n'
        '0 0 1000\n1 0.0002 0.0002\n0 0.001 1000\n1 0.0013 0.0003\n')
    output=tmp_path/'raw.csv'
    subprocess.run([str(tool),'export','-F','CSV-R','-o',str(output),str(vec)],check=True,capture_output=True)
    r=extract(output,s,m)['flows']['control']
    assert r['received']==2 and r['latency_seconds']['mean']==pytest.approx(.00025)


def test_failed_subprocess_and_cancel_are_terminal(tmp_path,monkeypatch):
    import sys,time
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'jobs.sqlite')
    runner.init()
    monkeypatch.setattr(runner,'environment_command',lambda script:[sys.executable,'-c','raise SystemExit(3)'])
    id=runner.submit(scenario())
    for _ in range(100):
        if runner.job(id)['status']=='failed': break
        time.sleep(.02)
    assert runner.job(id)['status']=='failed'
    assert not (tmp_path/'runs'/id/'metrics.json').exists()
    monkeypatch.setattr(runner,'environment_command',lambda script:[sys.executable,'-c','import time;time.sleep(30)'])
    id=runner.submit(scenario())
    for _ in range(100):
        if id in runner._processes: break
        time.sleep(.02)
    runner.cancel(id)
    assert runner.job(id)['status']=='cancelled'
    for _ in range(100):
        if id not in runner._processes: break
        time.sleep(.02)
    assert id not in runner._processes


def test_absent_sink_instrumentation_is_not_reported_as_loss(tmp_path):
    s=scenario(); s.flows=s.flows[:1]; m=legacy_manifest(s); src=m['flow_modules']['control']['source']
    p=tmp_path/'r.csv'; p.write_text(f'type,module,name,vectime,vecvalue\nvector,{src},packetLengths:vector,0,1000\n')
    with pytest.raises(ValueError,match='sink observations'): extract(p,s,m)


def test_named_simulations_create_update_copy_and_drafts(tmp_path,monkeypatch):
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'names.sqlite')
    from designer.api import app
    runner.init(); client=app.test_client()
    draft=Scenario(name='  My simulation  ').model_dump()
    saved=client.post('/api/scenarios',json=draft)
    assert saved.status_code==201
    id=saved.json['id']; assert saved.json['name']=='My simulation'
    assert client.post('/api/compile',json=draft).status_code==422
    assert client.post('/api/scenarios/validate',json=draft).json['name']=='My simulation'
    assert client.post('/api/scenarios',json=draft).status_code==409
    draft['name']='Renamed model'
    updated=client.put('/api/scenarios/'+id,json=draft)
    assert updated.status_code==200 and updated.json['id']==id
    assert client.get('/api/scenarios/'+id).json['scenario']['name']=='Renamed model'
    draft['name']='Independent copy'
    copy=client.post('/api/scenarios',json=draft)
    assert copy.status_code==201 and copy.json['id']!=id
    assert len(client.get('/api/scenarios').json)==2
    assert client.put('/api/scenarios/'+id,json=draft).status_code==409
    draft['name']='   '
    assert client.post('/api/scenarios',json=draft).status_code==422


def test_legacy_revisions_migrate_without_loss(tmp_path,monkeypatch):
    import sqlite3
    from designer import runner,storage
    dbpath=tmp_path/'migration.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE scenarios (id TEXT PRIMARY KEY, created REAL, body TEXT)')
        for i in [1,2]: db.execute('INSERT INTO scenarios VALUES (?,?,?)',(str(i),float(i),scenario().model_dump_json()))
    monkeypatch.setattr(runner,'DB',dbpath)
    runner.init(); runner.init()
    models=storage.list_models()
    assert len(models)==2 and len({m['name'] for m in models})==2
    with runner.connect() as db: assert db.execute('SELECT COUNT(*) FROM scenarios').fetchone()[0]==2
    assert {m['id'] for m in models}=={'1','2'}


def test_large_csv_vector_fields(tmp_path):
    s=scenario(); s.flows=s.flows[:1]; f=s.flows[0]
    f.interval=.0001; f.legacy_stop=4; s.experiment.duration=4.1
    m=legacy_manifest(s); mods=m['flow_modules']['control']
    count=30000; p=tmp_path/'large.csv'
    with p.open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=['type','module','name','vectime','vecvalue']); w.writeheader()
        w.writerow(dict(type='vector',module=mods['source'],name='packetLengths:vector',vectime=' '.join(f'{i*.0001:.8f}' for i in range(count)),vecvalue=' '.join(['1000']*count)))
        w.writerow(dict(type='vector',module=mods['sink'],name='packetLifeTime:vector',vectime=' '.join(f'{i*.0001+.00001:.8f}' for i in range(count)),vecvalue=' '.join(['.00001']*count)))
    assert p.stat().st_size>131072
    csv.field_size_limit(131072)  # Reset after imports: the reader must configure itself.
    r=extract(p,s,m)['flows']['control']
    assert r['generated']==count and r['received']==count and r['missing_at_horizon']==0


def test_completed_simulation_can_recover_extraction(tmp_path,monkeypatch):
    import time
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'recovery.sqlite')
    runner.init(); s=scenario(); s.flows=s.flows[:1]; manifest=legacy_manifest(s)
    id='recovery'; bundle=tmp_path/'runs'/id; bundle.mkdir(parents=True)
    body=json.dumps(dict(scenario=s.model_dump(),manifest=manifest))
    with runner.connect() as db: db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(id,time.time(),'failed','field larger than field limit',body))
    (bundle/'run.log').write_text('Simulation time limit reached\nEnd.\n')
    source=manifest['flow_modules']['control']['source']; sink=manifest['flow_modules']['control']['sink']
    (bundle/'raw.csv').write_text(f'type,module,name,vectime,vecvalue\nvector,{source},packetLengths:vector,0,1000\nvector,{sink},packetLifeTime:vector,.0002,.0002\n')
    runner.retry_extraction(id)
    for _ in range(100):
        if runner.job(id)['status'] in ['completed','failed']: break
        time.sleep(.02)
    assert runner.job(id)['status']=='completed'
    assert json.loads((bundle/'metrics.json').read_text())['flows']['control']['received']==1
    assert not (bundle/'results').exists()  # Recovery did not launch a new simulation.


def test_runner_completes_export_and_measurement_pipeline(tmp_path,monkeypatch):
    """Replay native vector output, then use the real scavetool and job pipeline."""
    import shlex,sys,time
    from designer import runner
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists(): pytest.skip('OMNeT++ result tool unavailable')
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'pipeline.sqlite')
    runner.init(); s=scenario(); s.flows=s.flows[:1]; manifest=compile_scenario(s)['manifest']
    modules=manifest['flow_modules']['control']
    recorder=next(iter(modules['receivers'].values()))['recorder']
    vector=('version 2\nrun replay\n'
        f'vector 0 "{modules["source"]}" "packetLengths:vector" TV\n'
        f'vector 1 "{recorder}" "control:meanBitElapsedTimePerPacket:vector" TV\n'
        '0 0 1000\n1 0.0002 0.0002\n')
    def command(script):
        if 'opp_scavetool' in script: return shlex.split(script)
        args=shlex.split(script)
        dest=Path(next(a.split('=',1)[1] for a in args if a.startswith('--result-dir=')))
        code=f'from pathlib import Path;p=Path({str(dest)!r});p.mkdir();(p/"replay.vec").write_text({vector!r});print("Simulation time limit reached")'
        return [sys.executable,'-c',code]
    monkeypatch.setattr(runner,'environment_command',command)
    id=runner.submit(s)
    for _ in range(100):
        if runner.job(id)['status'] in ['completed','failed']: break
        time.sleep(.05)
    assert runner.job(id)['status']=='completed',runner.job(id)['error']
    m=json.loads((tmp_path/'runs'/id/'metrics.json').read_text())
    assert m['flows']['control']['received']==1
    bundle=tmp_path/'runs'/id
    assert (bundle/'native-results.json').exists()
    assert not (bundle/'raw.csv').exists()
    assert json.loads((bundle/'pipeline.json').read_text())['stages']['export']['status']=='completed'


def test_json_reader_does_not_use_csv(tmp_path,monkeypatch):
    s=scenario(); s.flows=s.flows[:1]; m=legacy_manifest(s); mods=m['flow_modules']['control']
    p=tmp_path/'results.json'
    p.write_text(json.dumps({'one-run':{'vectors':[
        dict(module=mods['source'],name='packetLengths:vector',time=[0],value=[1000]),
        dict(module=mods['sink'],name='packetLifeTime:vector',time=[.0002],value=[.0002])
    ]}}))
    def forbidden(*a,**kw): raise AssertionError('CSV reader must not be called for JSON')
    monkeypatch.setattr(csv,'DictReader',forbidden)
    csv.field_size_limit(131072)
    assert extract(p,s,m)['flows']['control']['received']==1


def test_recovery_exports_native_results_without_legacy_csv(tmp_path,monkeypatch):
    import time
    from designer import runner
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists(): pytest.skip('OMNeT++ result tool unavailable')
    monkeypatch.setattr(runner,'DATA',tmp_path); monkeypatch.setattr(runner,'DB',tmp_path/'native.sqlite')
    runner.init(); s=scenario(); s.flows=s.flows[:1]; manifest=legacy_manifest(s)
    id='native-recovery'; bundle=tmp_path/'runs'/id; (bundle/'results').mkdir(parents=True)
    modules=manifest['flow_modules']['control']
    vector=('version 2\nrun native\n'
        f'vector 0 "{modules["source"]}" "packetLengths:vector" TV\n'
        f'vector 1 "{modules["sink"]}" "packetLifeTime:vector" TV\n'
        '0 0 1000\n1 0.0002 0.0002\n')
    native=bundle/'results/native.vec'; native.write_text(vector)
    (bundle/'run.log').write_text('Simulation time limit reached\n')
    body=json.dumps(dict(scenario=s.model_dump(),manifest=manifest))
    with runner.connect() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(id,time.time(),'failed','old extraction failed',body))
    assert runner.can_extract(id)
    # Reprocessing must never ask for an OMNeT++ runtime environment.
    def forbidden(script):raise AssertionError('Recovery cannot launch Cmdenv/opp_env')
    monkeypatch.setattr(runner,'environment_command',forbidden)
    runner.retry_extraction(id)
    for _ in range(100):
        if runner.job(id)['status'] in ['completed','failed']:break
        time.sleep(.02)
    assert runner.job(id)['status']=='completed',runner.job(id)['error']
    assert native.read_text()==vector
    assert not (bundle/'raw.csv').exists()
    metrics=json.loads((bundle/'metrics.json').read_text())
    assert metrics['results_pipeline']=='json-v1'
    assert metrics['results_source']=='native-results.json'
    assert metrics['recovered_from_existing_outputs'] is True


def test_mixed_json_runs_rejected(tmp_path):
    from designer.results import read_results
    p=tmp_path/'two.json';p.write_text(json.dumps({'seed1':{},'seed2':{}}))
    with pytest.raises(ValueError,match='exactly one'):read_results(p)


def test_ethernet_apps_tagger_clocks_and_burst_generation():
    s=scenario();s.flows=s.flows[:1];f=s.flows[0]
    f.sending_mode='burst';f.burst_size=7;f.burst_period=.02;f.start=.003;next(n for n in s.nodes if n.id==f.source).clock_drift_ppm=100
    result=compile_scenario(s);ini=result['files']['omnetpp.ini']
    assert 'EthernetSourceApp' in ini and 'EthernetSinkApp' in ini
    assert 'UdpSourceApp' not in ini and 'destPort' not in ini
    assert '.tagger.typename = "PacketTagger"' in ini
    assert '.tagger.vlanId = 10' in ini and '.tagger.pcp = 4' in ini
    assert '.tagger.encapsulationProtocols = ["ieee8021qctag"]' in ini
    assert 'seq("control") % 7 == 6' in ini
    assert '.source.initialProductionOffset = 0.003s' in ini
    assert '.oscillator.driftRate = 100ppm' in ini
    assert '.source.clockModule = "^.^.clock"' in ini
    assert '.measurementRecorder.packetFilter = "control-*"' in ini
    assert 'ned/ShapedQueue.ned' not in result['files']
    assert 'ned/FlowReceiver.ned' not in result['files']
    assert 'extends TsnNetworkBase' in result['files']['ned/Scenario.ned']
    assert '*.sw0.macTable.forwardingTable' in ini and '"vlan": 10' in ini
    assert result['manifest']['measurement_version']==3


def test_independent_classes_queue_count_and_composed_mapping():
    s=scenario();p=s.nodes[-1].ports[2]
    p.class_to_queue=[1,0,1];p.pcp_map=[0,1,2,0,2,1,0,2]
    result=compile_scenario(s);ini=result['files']['omnetpp.ini']
    assert '*.sw0.eth[2].macLayer.queue.typename = "Ieee8021qTimeAwareShaper"' in ini
    assert '*.sw0.eth[2].macLayer.queue.numTrafficClasses = 2' in ini
    expected=[p.class_to_queue[c] for c in p.pcp_map]
    assert '*.sw0.eth[2].macLayer.queue.classifier.pcpToGateIndex = '+json.dumps(expected) in ini
    assert '*.sw0.eth[2].macLayer.queue.transmissionSelectionAlgorithm[1].typename = "Ieee8021qCreditBasedShaper"' in ini
    assert result['paths']['control']['listener'][-1]['traffic_class']==2
    assert result['paths']['control']['listener'][-1]['queue']==1
    p.class_to_queue[0]=9
    with pytest.raises(InvalidScenario,match='Missing physical queue'):compile_scenario(s)


def test_sending_modes_and_burst_budget():
    s=scenario();s.flows=s.flows[:1];f=s.flows[0]
    f.sending_mode='once'
    assert '.source.productionInterval = inf s' in compile_scenario(s)['files']['omnetpp.ini']
    f.sending_mode='periodic'
    assert 'seq(' not in compile_scenario(s)['files']['omnetpp.ini']
    f.sending_mode='burst';f.burst_size=100000;f.burst_period=.001
    with pytest.raises(InvalidScenario,match='two million'):compile_scenario(s)


def test_sequence_numbers_distinguish_same_time_burst_packets(tmp_path):
    s=scenario();s.flows=s.flows[:1];f=s.flows[0];f.sending_mode='burst';f.burst_size=3
    manifest=legacy_manifest(s);manifest['measurement_version']=2;mods=manifest['flow_modules']['control'];mods.update(source_sequence='sequenceNumber:vector',sink_sequence='sequenceNumber:vector')
    def vector(role,name,time,value):return dict(module=mods[role],name=name,time=time,value=value)
    # Sequences 0,1,2 were generated at the same time. Sequence 1 is lost;
    # sequence 2 is duplicated. Count two deliveries, not one or three.
    data={'burst':{'vectors':[
        vector('source','packetLengths:vector',[0,0,0],[1000]*3),
        vector('source','sequenceNumber:vector',[0,0,0],[0,1,2]),
        vector('sink','packetLifeTime:vector',[.0001,.0003,.0004],[.0001,.0003,.0004]),
        vector('sink','sequenceNumber:vector',[.0001,.0003,.0004],[0,2,2])
    ]}}
    path=tmp_path/'burst.json';path.write_text(json.dumps(data))
    r=extract(path,s,manifest)['flows']['control']
    assert r['generated']==3 and r['received']==2 and r['missing_at_horizon']==1
    assert r['duplicates']==1
    assert r['ipdv_seconds']['count']==0  # Do not bridge the missing sequence.


def test_old_json_defaults_and_draft_round_trip():
    s=scenario();assert s.flows[0].sending_mode=='periodic' and s.flows[0].clock_drift_ppm==0
    assert s.nodes[0].ports[0].class_to_queue==[0,1]
    copy=Scenario.model_validate_json(s.model_dump_json())
    assert copy.model_dump()==s.model_dump()


def test_generated_ethernet_ned_native_syntax(tmp_path):
    import subprocess
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_nedtool'
    if not tool.exists():pytest.skip('Native NED syntax checker unavailable')
    result=compile_scenario(scenario())
    files=[]
    for name,text in result['files'].items():
        if name.endswith('.ned'):
            path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);files.append(str(path))
    subprocess.run([str(tool),'validate',*files],check=True,capture_output=True)


def test_start_offset_uses_local_clock_not_simulation_time():
    s=scenario();s.flows=s.flows[:1];f=s.flows[0]
    f.start=1.05;node=next(n for n in s.nodes if n.id==f.source);node.clock_drift_ppm=100000
    # A 10% faster local clock reaches 1.05 s at simulation time ~0.955 s.
    compile_scenario(s)
    node.clock_drift_ppm=-100000
    with pytest.raises(InvalidScenario,match='before simulation end'):compile_scenario(s)


def test_ethernet_socket_commands_bypass_end_device_bridges():
    """BIND is a Request: no endpoint packet-only bridge may intercept it."""
    for path in (ROOT/'scenarios').glob('*.json'):
        s=Scenario.model_validate_json(path.read_text())
        result=compile_scenario(s);ini=result['files']['omnetpp.ini']
        assert result['manifest']['compiler_version']=='3.3'
        for n in s.nodes:
            prefix=f'*.{n.id}'
            if n.kind=='device':
                assert f'{prefix}.hasBridging = false' in ini
                assert f'{prefix}.ethernet.hasSocketSupport = true' in ini
                assert f'{prefix}.hasIncomingStreams = true' in ini
                assert f'{prefix}.hasOutgoingStreams = true' in ini
                assert f'{prefix}.bridging.' not in ini
            else:
                assert f'{prefix}.hasBridging = false' not in ini
                assert f'{prefix}.bridging.vlanPolicy.typename = "VlanPolicyLayer"' in ini
        assert '.measurementRecorder.typename = "FlowMeasurementRecorder"' in ini


def test_two_switch_ethernet_native_execution(tmp_path,monkeypatch):
    """Host-side runtime regression; enable with TSN_NATIVE_TESTS=1."""
    import os,time
    if os.environ.get('TSN_NATIVE_TESTS')!='1':
        pytest.skip('Set TSN_NATIVE_TESTS=1 inside a working opp_env environment')
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'native.sqlite')
    runner.init()
    s=Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-2.json').read_text())
    s.experiment.duration=.02
    id=runner.submit(s)
    deadline=time.monotonic()+45
    while runner.job(id)['status'] not in ['completed','failed','cancelled'] and time.monotonic()<deadline:
        time.sleep(.1)
    status=runner.job(id)
    if status['status'] not in ['completed','failed','cancelled']:runner.cancel(id)
    log=tmp_path/'runs'/id/'run.log'
    assert status['status']=='completed',(status['error'],log.read_text(errors='replace') if log.exists() else '')
    metrics=json.loads((tmp_path/'runs'/id/'metrics.json').read_text())
    for flow in metrics['flows'].values():
        assert flow['generated']>0
        assert 0<flow['received']<=flow['generated']
        assert flow['generated']==flow['received']+flow['missing_at_horizon']
        assert flow['deadline_denominator']+flow['deadline_unmatured']==flow['generated']
