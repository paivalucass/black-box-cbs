import csv
import json
from pathlib import Path
import pytest
from designer.compiler import compile_scenario, InvalidScenario
from designer.schema import Scenario
from designer.metrics import extract

ROOT=Path(__file__).resolve().parents[1]
def scenario(): return Scenario.model_validate_json((ROOT/'scenarios/line-1.json').read_text())

def test_templates_compile():
    for p in (ROOT/'scenarios').glob('*.json'):
        result=compile_scenario(Scenario.model_validate_json(p.read_text()))
        assert 'user-interface = Cmdenv' in result['files']['omnetpp.ini']
        assert 'ethg++' not in result['files']['ned/Scenario.ned']
        assert result['paths']['control'][-1]['node'].startswith('sw')
        assert 'PacketAtCollectionEndDropper' in result['files']['omnetpp.ini']

def test_layout_does_not_change_semantic_identity():
    s=scenario(); first=compile_scenario(s)
    s.nodes[0].x+=50
    assert first['manifest']['semantic_hash']==compile_scenario(s)['manifest']['semantic_hash']

def test_vlan_and_class_errors():
    s=scenario(); s.nodes[0].ports[0].vlans=[20]
    with pytest.raises(InvalidScenario,match='not permitted'): compile_scenario(s)
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


def test_deadline_window():
    s=scenario(); s.experiment.duration=1
    with pytest.raises(InvalidScenario,match='deadlines'): compile_scenario(s)


def test_cohort_metrics_missing_deadlines_duplicates(tmp_path):
    s=scenario(); s.flows=s.flows[:1]; s.experiment.warmup=.001; f=s.flows[0]; f.stop=.004; f.deadline=.001
    m=compile_scenario(s)['manifest']; mods=m['flow_modules']['control']
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
    s=scenario(); s.flows=s.flows[:1]; m=compile_scenario(s)['manifest']; src=m['flow_modules']['control']['source']
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
    s=scenario(); s.flows=s.flows[:1]; m=compile_scenario(s)['manifest']; mods=m['flow_modules']['control']
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
    s=scenario(); s.flows=s.flows[:1]; m=compile_scenario(s)['manifest']; src=m['flow_modules']['control']['source']
    p=tmp_path/'r.csv'; p.write_text(f'type,module,name,vectime,vecvalue\nvector,{src},packetLengths:vector,0,1000\n')
    with pytest.raises(ValueError,match='sink observations'): extract(p,s,m)
