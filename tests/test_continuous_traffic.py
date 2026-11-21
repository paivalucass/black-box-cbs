"""Continuous generation through simulation end, with horizon-aware accounting."""
import json
from pathlib import Path
import pytest
from designer.compiler import compile_scenario, InvalidScenario
from designer.schema import Scenario
from designer.metrics import extract

ROOT=Path(__file__).resolve().parents[1]


def scenario():
    data=json.loads((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())
    data['flows']=data['flows'][:1]
    data['flows'][0]['stop']=.001 # Legacy settings must not terminate a new run.
    return Scenario.model_validate(data)


def test_new_periodic_runs_have_no_stop_condition_or_exposed_stop_setting():
    s=scenario();result=compile_scenario(s);ini=result['files']['omnetpp.ini']
    assert '.source.productionInterval = 0.001s' in ini
    assert 'simTime()' not in ini
    assert 'sim-time-limit = 1.1s' in ini
    assert result['manifest']['generation_horizon']=='simulation_end'
    assert 'stop' not in json.loads(result['files']['scenario.json'])['flows'][0]
    assert 'legacy_stop' not in result['files']['scenario.json']
    first=result['manifest']['semantic_hash']
    s.flows[0].legacy_stop=.00001
    assert compile_scenario(s)['manifest']['semantic_hash']==first


def test_bursts_keep_repeating_and_once_mode_remains_a_single_message():
    s=scenario();f=s.flows[0];f.sending_mode='burst';f.burst_size=7;f.burst_period=.02
    ini=compile_scenario(s)['files']['omnetpp.ini']
    assert 'seq("control") % 7 == 6 ? 0.02s : 0s' in ini
    assert 'inf s' not in ini and 'simTime()' not in ini
    f.sending_mode='once'
    assert '.source.productionInterval = inf s' in compile_scenario(s)['files']['omnetpp.ini']


def test_packet_budget_covers_the_full_simulation_and_ignores_legacy_stop():
    s=scenario();s.flows[0].interval=1e-6;s.experiment.duration=3
    with pytest.raises(InvalidScenario,match='two million'):compile_scenario(s)
    s.experiment.duration=1
    compile_scenario(s)
    s.experiment.warmup=1
    with pytest.raises(InvalidScenario,match='Warm-up'):compile_scenario(s)


def test_late_generations_are_counted_without_inventing_deadline_misses(tmp_path):
    s=scenario();s.flows[0].legacy_stop=1
    manifest=compile_scenario(s)['manifest'];mods=manifest['flow_modules']['control']
    recorder=mods['receivers']['listener']['recorder']
    data={'one':{'vectors':[
        dict(module=mods['source'],name='packetLengths:vector',time=[.999,1.005,1.095,1.1],value=[1000]*4),
        dict(module=recorder,name='control:meanBitElapsedTimePerPacket:vector',time=[1.001,1.006],value=[.002,.001]),
    ]}}
    path=tmp_path/'results.json';path.write_text(json.dumps(data))
    result=extract(path,s,manifest)['flows']['control']
    assert result['generated']==3 and result['received']==2 and result['missing_at_horizon']==1
    assert result['cohort_end']==1.1
    assert result['deadline_denominator']==2 and result['deadline_unmatured']==1
    assert result['deadline_misses']==0
    assert result['goodput_bps']==pytest.approx(2*1000*8/1.1)
    # Recovering a frozen pre-3.2 native archive must still honor its old cutoff.
    old={k:v for k,v in manifest.items() if k!='generation_horizon'}
    result=extract(path,s,old)['flows']['control']
    assert result['generated']==1 and result['received']==1 and result['cohort_end']==1


def test_no_matured_deadlines_means_unavailable_ratio_not_failure(tmp_path):
    s=scenario();f=s.flows[0];f.deadline=2
    manifest=compile_scenario(s)['manifest'];mods=manifest['flow_modules']['control']
    path=tmp_path/'late.json';path.write_text(json.dumps({'one':{'vectors':[
        dict(module=mods['source'],name='packetLengths:vector',time=[1.095],value=[1000])
    ],'scalars':[dict(module=mods['receivers']['listener']['sink'],name='packets:count',value=0)]}}))
    result=extract(path,s,manifest)['flows']['control']
    assert result['deadline_denominator']==0 and result['deadline_unmatured']==1
    assert result['deadline_misses']==0 and result['deadline_miss_ratio'] is None
    assert result['missing_at_horizon']==1
