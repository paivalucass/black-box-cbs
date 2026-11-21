"""Research defaults must exercise a real topology and a diverse, bounded workload."""
import json
from pathlib import Path
import time
import os
import pytest
from designer.schema import Scenario
from designer.compiler import compile_scenario
from scripts.generate_research_templates import PROFILES, generate

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def catalogue():
    return list(generate())


def test_template_menu_contains_only_the_new_research_families(tmp_path,monkeypatch,catalogue):
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'templates.sqlite')
    from designer.api import app
    runner.init();client=app.test_client()
    templates=client.get('/api/templates').json
    assert len(templates)==5
    assert {s['ned_name'] for s in templates}=={p.network for p in PROFILES}
    assert min(len(s['flows']) for s in templates)>=198
    # The largest real model fits the public API's request limit and compiles there.
    largest=max(templates,key=lambda s:len(s['flows']))
    response=client.post('/api/compile',json=largest)
    assert response.status_code==200
    assert response.json['manifest']['network'].endswith('.LeafSpineCompute')


def test_generated_files_are_reproducible_and_match_committed_templates(catalogue):
    for profile,s,result,info in catalogue:
        assert (ROOT/'scenarios'/profile.filename).read_text()==s.model_dump_json(indent=2)+'\n'
        assert info['expected_source_packets']<50000
        assert len(json.dumps(s.model_dump()).encode())<4*1024*1024
        assert s.experiment.record_link_occupancy
        assert all('stop' not in f for f in json.loads(s.model_dump_json())['flows'])


def test_each_family_has_diverse_sources_classes_bursts_and_multicast(catalogue):
    for profile,s,result,info in catalogue:
        assert info['devices']>=48 and info['switches']>=12
        assert len(s.flows)>=4*info['devices']
        assert info['burst_flows']>=.3*len(s.flows)
        assert info['multicast_flows']>=20
        assert {f.source for f in s.flows}=={n.id for n in s.nodes if n.kind=='device'}
        assert {f.pcp for f in s.flows}==set(range(8))
        assert {f.vlan for f in s.flows}=={10,20,30,40}
        assert len({f.payload for f in s.flows})>=8
        assert len({f.burst_size for f in s.flows if f.sending_mode=='burst'})>=4
        assert any(n.clock_drift_ppm<0 for n in s.nodes)
        assert any(n.clock_drift_ppm>0 for n in s.nodes)
        assert all(len(n.ports)==1 for n in s.nodes if n.kind=='device')


def test_every_physical_link_carries_resolved_traffic_and_only_active_cbs_is_optimized(catalogue):
    for profile,s,result,info in catalogue:
        assert info['active_links']==info['links']
        nodes={n.id:n for n in s.nodes}
        active={f'{l["node"]}/{l["port"]}/{l["queue"]}' for l in result['loads']
                if nodes[l['node']].ports[l['port']].queues[l['queue']].shaper=='cbs'}
        assert {v['key'] for v in result['manifest']['variables']}==active
        assert len(active)>=80
        for n in s.nodes:
            for p in n.ports:
                assert len(p.queues)==8 and p.pcp_map==list(range(8)) and p.class_to_queue==list(range(8))
                assert p.higher_queue_index_first
                assert all(q.capacity>0 for q in p.queues)
                if n.kind=='device':assert all(q.shaper=='sp' for q in p.queues)


def test_routed_networks_and_overload_cases_are_distinct(catalogue):
    by_topology={p.topology:(s,result,info) for p,s,result,info in catalogue}
    for kind,(s,result,info) in by_topology.items():
        if kind=='cascade':
            assert info['max_hops']>=9
            assert result['diagnostics'] # Deliberate reservation stress, not an invalid model.
            transit=[l for l in s.links if l.a.node.startswith('Transit') and l.b.node.startswith('Transit')]
            assert sum(l.bitrate==100e6 for l in transit)==2
        else:
            assert len(s.links)>=len(s.nodes) # Physical cycles, safely statically forwarded.
            assert not result['diagnostics'] # No accidental long-run queue reservation overload.
            assert .6<=info['max_port_offered_ratio']<1 # Deliberate load, not a mostly idle large graph.
    fabric,_,_=by_topology['fabric']
    spine_links=[l for l in fabric.links if l.a.node.startswith('Spine') or l.b.node.startswith('Spine')]
    assert len(spine_links)==48
    for l in spine_links:
        endpoint=l.a if l.a.node.startswith('Spine') else l.b
        spine=next(n for n in fabric.nodes if n.id==endpoint.node)
        assert spine.ports[endpoint.port].vlans==[10*(int(spine.id[-2:])+1)]


def test_complex_research_native_execution(tmp_path,monkeypatch):
    if os.environ.get('TSN_NATIVE_TESTS')!='1':pytest.skip('Requires a working host opp_env environment')
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'research.sqlite')
    runner.init();s=Scenario.model_validate_json((ROOT/'scenarios/01-automotive-zonal.json').read_text())
    s.experiment.duration=.08
    id=runner.submit(s);until=time.monotonic()+90
    while runner.job(id)['status'] not in ['completed','failed','cancelled'] and time.monotonic()<until:time.sleep(.1)
    state=runner.job(id)
    if state['status'] not in ['completed','failed','cancelled']:runner.cancel(id)
    log=tmp_path/'runs'/id/'run.log'
    assert state['status']=='completed',(state['error'],log.read_text(errors='replace') if log.exists() else '')
    metrics=json.loads((tmp_path/'runs'/id/'metrics.json').read_text())
    assert len(metrics['flows'])==sum(len(f.receivers) for f in s.flows)
    assert sum(f['received'] for f in metrics['flows'].values())>1000
    assert metrics['link_occupancy']['available'] and not metrics['link_occupancy']['partial']
