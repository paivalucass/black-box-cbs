"""Behavioral contract for the sample-style INET compiler and native telemetry."""
import json
import subprocess
from pathlib import Path
import pytest
from designer.compiler import compile_scenario, InvalidScenario
from designer.schema import Scenario
from designer.metrics import extract
from designer.results import export_command

ROOT=Path(__file__).resolve().parents[1]

def sample():
    return Scenario.model_validate_json((ROOT/'scenarios/reference/sample-automotive.json').read_text())

def line():
    return Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())

def test_sample_topology_apps_clocks_and_native_paths():
    s=sample();r=compile_scenario(s);ini=r['files']['omnetpp.ini']
    assert (len(s.nodes),len(s.links),len(s.flows))==(20,21,18)
    assert sum(len(f.receivers) for f in s.flows)==27
    assert 'network AutomotiveSample extends TsnNetworkBase' in r['files']['ned/AutomotiveSample.ned']
    assert 'SW_Core.ethg[0] <--> Eth10G { length = 3m; } <--> SW_ADAS.ethg[0]' in r['files']['ned/AutomotiveSample.ned']
    assert '*.SW_Core.eth[0].macLayer.queue.typename = "PriorityQueue"' in ini
    assert '*.SW_Core.eth[0].macLayer.queue.scheduler.reverseOrder = true' in ini
    assert '*.Central_HPC.eth[0].macLayer.queue.typename = "Ieee8021qTimeAwareShaper"' in ini
    assert '*.Central_HPC.eth[0].macLayer.queue.transmissionSelectionAlgorithm[1].idleSlope = 300000000.0bps' in ini
    assert '*.SW_ADAS.eth[2].macLayer.queue.transmissionSelectionAlgorithm[1].idleSlope = 100000000.0bps' in ini
    assert '*.FrontCam.clock.oscillator.driftRate = 15.0ppm' in ini
    assert 'SettableClock' in ini and 'nominalTickLength = 10ns' in ini
    assert 'seq("Lidar_Points") % 10 == 9' in ini
    assert 'seq("Telematics_Down") % 4 == 3' in ini
    assert 'FlowMeasurementStarter' in ini and 'FlowMeasurementRecorder' in ini
    assert set(r['files'])=={'ned/package.ned','ned/AutomotiveSample.ned','omnetpp.ini','scenario.json','resolved.json','manifest.json','run.sh'}
    assert all(word not in ini for word in ['SwitchProcessing','PhyDelay','ShapedQueue','flowClock','FlowReceiver'])
    # Sample's deliberate route via SW_Front, rather than the shorter Rear/Core edge.
    assert [h['node'] for h in r['paths']['Body_Status']['Central_HPC']]==['BodyCtrl','SW_Rear','SW_Front','SW_Core']


def test_multicast_replication_and_shared_load_are_exact():
    s=sample();f=next(f for f in s.flows if f.id=='ADAS_Objects');s.flows=[f]
    r=compile_scenario(s);rows=r['forwarding']['SW_Core']
    assert {e['interface'] for e in rows if e['address']==f.multicast_mac}=={'eth4','eth5'}
    assert all(e['vlan']==10 for e in rows)
    # Shared source link is charged once, not once per receiver.
    source=next(x for x in r['loads'] if x['node']=='ADAS_HPC')
    assert source['bps']==pytest.approx((600+22+20)*8/.02)
    assert set(r['manifest']['flow_modules'][f.id]['receivers'])=={'Central_HPC','Logger'}


def test_automatic_multicast_routes_on_cyclic_physical_network():
    s=sample()
    for f in s.flows:f.routes={}
    r=compile_scenario(s)
    for f in s.flows:
        assert set(r['paths'][f.id])==set(f.receivers)
        for receiver,path in r['paths'][f.id].items():
            assert path[0]['node']==f.source
            assert len({h['node'] for h in path})==len(path)
    f=next(f for f in s.flows if f.multicast_mac)
    f.multicast_mac=None
    assert compile_scenario(s)['addresses'][f.id].startswith('01:00:00:')


@pytest.mark.parametrize('edit',[
    lambda f:setattr(f,'receivers',['missing']),
    lambda f:setattr(f,'multicast_mac','02:00:00:00:00:01'),
    lambda f:setattr(f,'multicast_mac','01:80:c2:00:00:00'),
    lambda f:setattr(f,'routes',{'listener':['talker','missing','listener']}),
    lambda f:setattr(f,'routes',{'listener':['talker','sw0','talker','listener']}),
])
def test_invalid_receiver_mac_and_routes_return_diagnostics(edit):
    s=line();s.flows=s.flows[:1];edit(s.flows[0])
    with pytest.raises(InvalidScenario):compile_scenario(s)


def test_inconsistent_multicast_parents_rejected():
    s=sample();s.flows=[next(f for f in s.flows if f.id=='Time_Sync')];f=s.flows[0]
    # One branch reaches Front through Core, another through Rear: not a rooted tree.
    f.routes['SteeringCtrl']=['Central_HPC','SW_Core','SW_Rear','SW_Front','SteeringCtrl']
    with pytest.raises(InvalidScenario,match='source-rooted tree'):compile_scenario(s)


def test_legacy_models_migrate_and_conflicting_clocks_require_resolution():
    data=json.loads((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())
    first=data['flows'][0];data['flows']=[first,{**first,'id':'second','clock_drift_ppm':20}]
    s=Scenario.model_validate(data)
    assert s.schema_version==4 and s.flows[0].receivers==['listener']
    assert s.flows[0].destination==''
    with pytest.raises(InvalidScenario,match='Conflicting legacy flow clocks'):compile_scenario(s)
    next(n for n in s.nodes if n.id==first['source']).clock_drift_ppm=5
    r=compile_scenario(s)
    assert '.clock.oscillator.driftRate = 5ppm' in r['files']['omnetpp.ini']
    assert 'flowClock' not in r['files']['omnetpp.ini']
    assert Scenario.model_validate_json(r['files']['scenario.json']).model_dump()==s.model_dump()


def test_native_multicast_metrics_keep_burst_samples_and_receivers_separate(tmp_path):
    s=line();s.flows=s.flows[:1];f=s.flows[0]
    # The other existing source is also a valid receiver on this star.
    f.receivers=['listener','background'];f.sending_mode='burst';f.burst_size=3
    s.experiment.warmup=.001
    m=compile_scenario(s)['manifest'];mods=m['flow_modules'][f.id]
    vectors=[dict(module=mods['source'],name='packetLengths:vector',time=[0,.001,.001,.001],value=[1000]*4)]
    for receiver,delays in [('listener',[.0001,.0002,.0003]),('background',[.0004,.0006])]:
        module=mods['receivers'][receiver]['recorder']
        # A pre-warm-up generation arriving after warm-up must be excluded.
        vectors.append(dict(module=module,name='control:meanBitElapsedTimePerPacket:vector',time=[.00105]+[.001+d for d in delays],value=[.00105]+delays))
        vectors.append(dict(module=module,name='control:meanTotalBitQueueingTimePerPacket:vector',time=[.00105]+[.001+d for d in delays],value=[0]+[d/2 for d in delays]))
    path=tmp_path/'native.json';path.write_text(json.dumps({'one':{'vectors':vectors}}))
    result=extract(path,s,m)
    listener=result['flows']['control@listener'];other=result['flows']['control@background']
    assert (listener['generated'],listener['received'],other['received'])==(3,3,2)
    assert other['missing_at_horizon']==1
    assert listener['latency_seconds']['mean']==pytest.approx(.0002)
    assert listener['ipdv_seconds']['mean']==pytest.approx(.0001)
    assert listener['delay_breakdown_seconds']['queueing']['mean']==pytest.approx(.0001)
    assert listener['duplicates'] is None and listener['out_of_order'] is None
    assert listener['deadline_denominator']==3 and other['deadline_misses']==1


def test_native_zero_receptions_and_missing_instrumentation(tmp_path):
    s=line();s.flows=s.flows[:1];m=compile_scenario(s)['manifest'];mods=m['flow_modules']['control'];sink=mods['receivers']['listener']
    data={'one':{'vectors':[dict(module=mods['source'],name='packetLengths:vector',time=[0],value=[1000])], 'scalars':[dict(module=sink['sink'],name='packets:count',value=0)]}}
    p=tmp_path/'zero.json';p.write_text(json.dumps(data));r=extract(p,s,m)['flows']['control']
    assert r['received']==0 and r['latency_seconds']['mean'] is None and r['deadline_misses']==1
    data['one']['scalars']=[];p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Missing sink instrumentation'):extract(p,s,m)


def test_official_export_retains_native_flow_demux_breakdown_and_credits(tmp_path):
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_scavetool'
    if not tool.exists():pytest.skip('OMNeT++ result tool unavailable')
    s=line();s.flows=s.flows[:1];m=compile_scenario(s)['manifest'];mods=m['flow_modules']['control'];recorder=mods['receivers']['listener']['recorder']
    vec=tmp_path/'native.vec';out=tmp_path/'native.json'
    vec.write_text('version 2\nrun native\n'
        f'vector 0 "{mods["source"]}" "packetLengths:vector" TV\n'
        f'vector 1 "{recorder}" "control:meanBitElapsedTimePerPacket:vector" TV\n'
        f'vector 2 "{recorder}" "control:meanTotalBitQueueingTimePerPacket:vector" TV\n'
        'vector 3 "Scenario.sw0.eth[2].macLayer.queue.transmissionSelectionAlgorithm[1]" "numCredits:vector" TV\n'
        '0 0 1000\n1 .0002 .0002\n2 .0002 .00005\n3 .0001 -50\n')
    subprocess.run(export_command(tool,[vec],out,m),check=True,capture_output=True)
    r=extract(out,s,m)
    assert r['flows']['control']['delay_breakdown_seconds']['queueing']['mean']==.00005
    assert r['traces'][0]['samples']==[[.0001,-50]]


def test_all_template_ned_files_pass_native_syntax(tmp_path):
    tool=ROOT.parent/'omnetpp-6.4.0/bin/opp_nedtool'
    if not tool.exists():pytest.skip('OMNeT++ NED tool unavailable')
    for i,p in enumerate((ROOT/'scenarios').glob('*.json')):
        r=compile_scenario(Scenario.model_validate_json(p.read_text()));files=[]
        for name,content in r['files'].items():
            if not name.endswith('.ned'):continue
            path=tmp_path/str(i)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content);files.append(str(path))
        subprocess.run([str(tool),'validate',*files],check=True,capture_output=True)


@pytest.mark.parametrize('filename',['traffic-burst-drift.json','sample-automotive.json'])
def test_sample_and_burst_native_execution(filename,tmp_path,monkeypatch):
    """Run only in a genuine host opp_env; no simulation substitute is used."""
    import os,time
    if os.environ.get('TSN_NATIVE_TESTS')!='1':pytest.skip('Set TSN_NATIVE_TESTS=1 in a working opp_env environment')
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'runtime.sqlite')
    runner.init();s=Scenario.model_validate_json(((ROOT/'scenarios/reference'/filename) if filename=='sample-automotive.json' else (ROOT/'tests/fixtures/legacy-scenarios'/filename)).read_text())
    s.experiment.duration=.1
    job_id=runner.submit(s);until=time.monotonic()+60
    while runner.job(job_id)['status'] not in ['completed','failed','cancelled'] and time.monotonic()<until:time.sleep(.1)
    job=runner.job(job_id)
    if job['status'] not in ['completed','failed','cancelled']:runner.cancel(job_id)
    log=tmp_path/'runs'/job_id/'run.log'
    assert job['status']=='completed',(job['error'],log.read_text(errors='replace') if log.exists() else '')
    metrics=json.loads((tmp_path/'runs'/job_id/'metrics.json').read_text())
    assert metrics['measurement_version']==3
    assert metrics['link_occupancy']['available'],metrics['link_occupancy'].get('reason')
    assert not metrics['link_occupancy']['partial']
    assert any(d['packet_count']>0 for d in metrics['link_occupancy']['directions'])
    assert len(metrics['flows'])==sum(len(f.receivers) for f in s.flows)
    for flow in metrics['flows'].values():
        assert flow['generated']>0
        assert 0<flow['received']<=flow['generated']
        assert flow['generated']==flow['received']+flow['missing_at_horizon']
        assert flow['deadline_denominator']+flow['deadline_unmatured']==flow['generated']
        assert flow['latency_seconds']['mean']>0


def test_ned_keyword_names_rejected_before_generation():
    s=line();s.ned_name='network'
    with pytest.raises(InvalidScenario,match='reserved keyword'):compile_scenario(s)
    s=line();s.nodes[0].id='simple'
    with pytest.raises(InvalidScenario,match='reserved NED keyword'):compile_scenario(s)


def test_cli_extract_uses_frozen_legacy_manifest(tmp_path,monkeypatch,capsys):
    import sys
    from designer import cli
    s=line();s.flows=s.flows[:1];scenario_path=tmp_path/'scenario.json';scenario_path.write_text(s.model_dump_json())
    manifest=dict(measurement_version=1,flow_modules={'control':dict(source='Legacy.old.source',sink='Legacy.old.sink')})
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    output=tmp_path/'native-results.json';output.write_text(json.dumps({'legacy':{'vectors':[
        dict(module='Legacy.old.source',name='packetLengths:vector',time=[0],value=[1000]),
        dict(module='Legacy.old.sink',name='packetLifeTime:vector',time=[.0002],value=[.0002])
    ]}}))
    def forbidden(*args,**kwargs):raise AssertionError('Archived extraction must use the frozen manifest instead of recompiling')
    monkeypatch.setattr(cli,'compile_scenario',forbidden)
    monkeypatch.setattr(sys,'argv',['designer.cli','extract',str(scenario_path),'--results',str(output)])
    cli.main()
    result=json.loads(capsys.readouterr().out)
    assert result['measurement_version']==1 and result['flows']['control']['received']==1
