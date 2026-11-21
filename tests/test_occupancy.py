"""Exact event-level intervals: native semantics, multicast and horizon censoring."""
import json
from pathlib import Path
import pytest
from designer.compiler import compile_scenario
from designer.schema import Scenario
from designer.occupancy import iter_json_array,process_link_occupancy,occupancy_window

ROOT=Path(__file__).resolve().parents[1]


def trace(tmp_path):
    s=Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())
    s.experiment.duration=.01
    m=compile_scenario(s)['manifest']
    direction=next(d for d in m['link_occupancy']['directions'] if d['source']=='talker')
    m['link_occupancy']['directions']=[direction]
    results=tmp_path/'results';results.mkdir()
    q=dict(eventNumber=10,type='QUEUED',module=direction['queue']+'.queue[1]',simulationTime=.001,duration=.0006)
    # Same event in two payload regions: only one queue interval may be counted.
    observation=dict(module=direction['observer'],simulationTime=.001,packetName='control-0',packetTreeId=7,packetEvents=[dict(events=[q]),dict(events=[q])])
    # A second packet starts at 9 ms and is still transmitting at the 10 ms horizon.
    partial=dict(module=direction['observer'],simulationTime=.009,packetName='control-1',packetTreeId=8,packetEvents=[])
    (results/direction['event_file']).write_text(json.dumps([observation,partial]))
    exported=tmp_path/'native.json';exported.write_text(json.dumps({'one':{'vectors':[
        dict(module=direction['transmitter'],name='transmitting:vector',time=[.001,.003,.009],value=[1,0,1]),
        dict(module=direction['transmitter'],name='control:flowTransmissionTime:vector',time=[.003],value=[.002]),
    ],'scalars':[dict(module=direction['transmitter'],name='propagationTime',value=.0005)]}}))
    return s,m,direction,exported


def test_compiler_observers_preserve_native_flow_tags_and_use_recorded_signals():
    s=Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())
    r=compile_scenario(s);ini=r['files']['omnetpp.ini']
    assert len(r['manifest']['link_occupancy']['directions'])==2*len(s.links)
    assert ' or packetEvent"' in ini
    assert '.cutthroughSink.typename = "FlowMeasurementRecorder"' in ini
    assert '.cutthroughSink.measure = "packetEvent"' in ini
    assert '.cutthroughSink.flowName = "__link_observer__"' in ini
    assert '.cutthroughSink.endMeasurement = true' in ini
    assert '${resultdir}/link-events-' in ini
    assert not any(f.id.startswith('_') for f in s.flows)
    s.experiment.record_link_occupancy=False
    disabled=compile_scenario(s)
    assert not disabled['manifest']['link_occupancy']['enabled']
    assert 'packetEvent' not in disabled['files']['omnetpp.ini']


def test_native_queue_wait_transmission_and_propagation_are_separate_exact_intervals(tmp_path):
    s,m,d,p=trace(tmp_path);metadata=process_link_occupancy(tmp_path,s,m,p)
    assert metadata['available'] and not metadata['partial']
    assert metadata['directions'][0]['packet_count']==2
    result=occupancy_window(tmp_path,metadata,d['key'],0,.01)
    assert [x['kind'] for x in result['segments']]==['queueing','transmission','propagation','transmission']
    a,b,c,last=result['segments']
    assert a['start']==pytest.approx(.0004) and a['end']==.001
    assert b['start']==.001 and b['end']==.003
    assert c['start']==.003 and c['end']==.0035
    assert last['start']==.009 and last['end']==.01 and last['censored']
    assert a['packet']=='control-0' and a['flow_id']=='control'
    assert result['total_matching']==4


def test_time_window_keeps_overlapping_queue_wait_and_explicit_truncation(tmp_path):
    s,m,d,p=trace(tmp_path);metadata=process_link_occupancy(tmp_path,s,m,p)
    # Queueing began before this window; it still intersects it and must appear.
    window=occupancy_window(tmp_path,metadata,d['key'],.0005,.0008)
    assert len(window['segments'])==1 and window['segments'][0]['kind']=='queueing'
    limited=occupancy_window(tmp_path,metadata,d['key'],0,.01,limit=2)
    assert limited['truncated'] and limited['total_matching']==4 and len(limited['segments'])==2
    with pytest.raises(ValueError):occupancy_window(tmp_path,metadata,d['key'],.01,.001)
    with pytest.raises(ValueError):occupancy_window(tmp_path,metadata,'unknown',0,.01)


def test_missing_or_inconsistent_observations_do_not_create_estimated_bars(tmp_path):
    s,m,d,p=trace(tmp_path)
    raw=tmp_path/'results'/d['event_file']
    observations=json.loads(raw.read_text());observations[0]['simulationTime']=.0015
    raw.write_text(json.dumps(observations))
    metadata=process_link_occupancy(tmp_path,s,m,p)
    assert not metadata['available'] and 'disagree' in metadata['reason']
    raw.unlink();metadata=process_link_occupancy(tmp_path,s,m,p)
    assert not metadata['available'] and 'missing' in metadata['reason']
    old=process_link_occupancy(tmp_path,s,{},p)
    assert not old['available'] and 'new simulation' in old['reason']


def test_packet_json_stream_handles_records_larger_than_csv_field_limit(tmp_path):
    path=tmp_path/'large.json';records=[dict(packetName='control-0',packetData='x'*150000),dict(packetName='control-1')]
    path.write_text(json.dumps(records))
    assert list(iter_json_array(path))==records
    path.write_text('[]');assert list(iter_json_array(path))==[]
    path.write_text('[{"a":1}')
    with pytest.raises(ValueError):list(iter_json_array(path))


def test_occupancy_api_selects_frozen_run_and_does_not_launch_simulator(tmp_path,monkeypatch):
    import time
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'api.sqlite')
    from designer.api import app
    runner.init();bundle=tmp_path/'runs'/'one';bundle.mkdir(parents=True)
    s,m,d,p=trace(bundle);metadata=process_link_occupancy(bundle,s,m,p)
    (bundle/'metrics.json').write_text(json.dumps(dict(link_occupancy=metadata)))
    with runner.connect() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('one',time.time(),'completed',None,json.dumps(dict(scenario=s.model_dump(),manifest=m))))
    def forbidden(*args,**kwargs):raise AssertionError('Reading a timeline cannot rerun the simulation')
    monkeypatch.setattr(runner,'submit',forbidden)
    client=app.test_client()
    response=client.get('/api/jobs/one/occupancy',query_string=dict(link=d['key'],start=0,end=.01,limit=2))
    assert response.status_code==200 and response.json['truncated']
    assert client.get('/api/jobs/one/occupancy',query_string=dict(link=d['key'],start='nan',end=.01)).status_code==422


def test_official_export_retains_transmitter_states_flow_durations_and_propagation(tmp_path):
    import subprocess
    from designer.results import export_command,read_results
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists():pytest.skip('OMNeT++ result exporter unavailable')
    s,m,d,p=trace(tmp_path)
    vec=tmp_path/'native.vec';sca=tmp_path/'native.sca';output=tmp_path/'selected.json'
    vec.write_text('version 2\nrun native\n'+f'vector 0 "{d["transmitter"]}" "transmitting:vector" TV\n'+f'vector 1 "{d["transmitter"]}" "control:flowTransmissionTime:vector" TV\n'+'0 .001 1\n0 .003 0\n1 .003 .002\n')
    sca.write_text('version 2\nrun native\n'+f'scalar "{d["transmitter"]}" "propagationTime" .0005\n')
    subprocess.run(export_command(tool,[vec,sca],output,m),check=True,capture_output=True)
    vectors,scalars=read_results(output)
    assert vectors[d['transmitter'],'transmitting:vector']==[(.001,1),(.003,0)]
    assert vectors[d['transmitter'],'control:flowTransmissionTime:vector']==[(.003,.002)]
    assert next(r['value'] for r in scalars if r['name']=='propagationTime')==.0005


def test_shared_result_pipeline_publishes_packet_timeline_with_latency(tmp_path,monkeypatch):
    """Official exporter + actual processing path, replaying a known hand trace."""
    import time
    from designer import runner
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists():pytest.skip('OMNeT++ result exporter unavailable')
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'pipeline.sqlite')
    runner.init();bundle=tmp_path/'runs'/'packet-replay';bundle.mkdir(parents=True)
    s,m,d,p=trace(bundle);s.flows=s.flows[:1]
    m['flow_modules']={'control':m['flow_modules']['control']}
    mods=m['flow_modules']['control'];recorder=mods['receivers']['listener']['recorder']
    vec=bundle/'results'/'replay.vec'
    vec.write_text('version 2\nrun native\n'+
        f'vector 0 "{mods["source"]}" "packetLengths:vector" TV\n'+
        f'vector 1 "{recorder}" "control:meanBitElapsedTimePerPacket:vector" TV\n'+
        f'vector 2 "{d["transmitter"]}" "transmitting:vector" TV\n'+
        f'vector 3 "{d["transmitter"]}" "control:flowTransmissionTime:vector" TV\n'+
        '0 0 8000\n0 .008 8000\n1 .005 .005\n2 .001 1\n2 .003 0\n2 .009 1\n3 .003 .002\n')
    (bundle/'results'/'replay.sca').write_text('version 2\nrun native\n'+f'scalar "{d["transmitter"]}" "propagationTime" .0005\n')
    body=dict(scenario=s.model_dump(),manifest=m)
    with runner.connect() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('packet-replay',time.time(),'extracting',None,json.dumps(body)))
    runner.process_results('packet-replay',bundle,body)
    assert runner.job('packet-replay')['status']=='completed'
    metrics=json.loads((bundle/'metrics.json').read_text())
    assert metrics['flows']['control']['received']==1
    assert metrics['link_occupancy']['available']
    assert occupancy_window(bundle,metrics['link_occupancy'],d['key'],0,.01)['total_matching']==4
