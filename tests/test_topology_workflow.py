"""Automatic interface allocation, cable models, fixed priority and route discovery."""
import json
from pathlib import Path
import pytest
from designer.schema import Scenario, Link, Port, assign_ports
from designer.compiler import compile_scenario, InvalidScenario
from designer.routing import RouteQuery, route_options

ROOT=Path(__file__).resolve().parents[1]


def line():
    return Scenario.model_validate_json((ROOT/'tests/fixtures/legacy-scenarios/line-1.json').read_text())


def mesh():
    # Four alternative paths, with both 3-link and 4-link choices.
    return Scenario.model_validate(dict(name='Route choices',nodes=[
        dict(id='sender'),dict(id='receiver'),dict(id='deadEnd'),
        *[dict(id=n,kind='switch') for n in ['a','b','c','d']],
    ],links=[dict(id=f'l{i}',a=dict(node=a),b=dict(node=b),length_m=10) for i,(a,b) in enumerate([
        ('sender','a'),('a','b'),('a','c'),('b','c'),('b','d'),('c','d'),('d','receiver'),('a','deadEnd')
    ])],flows=[dict(id='flow',source='sender',receivers=['receiver'])]))


def test_node_only_connections_allocate_contiguous_ports_and_native_cable_length():
    s=mesh()
    assert [p.index for p in next(n for n in s.nodes if n.id=='a').ports]==[0,1,2,3]
    assert all(l.a.port is not None and l.b.port is not None for l in s.links)
    assert all(l.delay==pytest.approx(50e-9) for l in s.links)
    result=compile_scenario(s)
    ned=result['files']['ned/Scenario.ned']
    assert 'length = 10m;' in ned and 'delay =' not in ned
    assert 'numEthInterfaces = 4' in ned
    assert s.schema_version==4


def test_removing_a_link_preserves_surviving_connection_queue_settings():
    s=line();switch=next(n for n in s.nodes if n.kind=='switch')
    survivor=next(l for l in s.links if l.a.node=='listener' or l.b.node=='listener')
    ep=survivor.a if survivor.a.node==switch.id else survivor.b
    switch.ports[ep.port].queues[1].idle_slope=12345678
    # Delete an earlier link, shifting the listener connection's port number.
    before=ep.port
    s.links=[l for l in s.links if l.a.node!='talker' and l.b.node!='talker']
    assign_ports(s)
    assert ep.port==before-1
    assert switch.ports[ep.port].queues[1].idle_slope==12345678
    # Normalization is idempotent; serialization/reload retains those settings.
    frozen=s.model_dump()
    assert Scenario.model_validate(frozen).model_dump()==frozen


def test_imported_indices_are_configuration_hints_not_port_assignments():
    data=mesh().model_dump()
    for n in data['nodes']:
        for p in n['ports']:p['index']+=20
    for l in data['links']:
        l['a']['port']+=20;l['b']['port']+=20
    s=Scenario.model_validate(data)
    assert s.links[0].a.port==0 and s.links[0].b.port==0
    assert [p.index for p in next(n for n in s.nodes if n.id=='a').ports]==[0,1,2,3]
    compile_scenario(s)


def test_additional_switch_connection_allocates_new_port_without_reusing_settings():
    s=line();switch=next(n for n in s.nodes if n.kind=='switch');count=len(switch.ports)
    s.nodes.append(s.nodes[0].model_copy(deep=True,update={'id':'extra','ports':[]}))
    s.links.append(Link(id='new',a={'node':'extra'},b={'node':switch.id},length_m=25))
    assign_ports(s)
    assert s.links[-1].b.port==count
    assert len(switch.ports)==count+1
    compile_scenario(s)


def test_legacy_delays_convert_to_lengths_and_length_is_authoritative():
    legacy=Link(id='old',a={'node':'a'},b={'node':'b'},delay=1e-6)
    assert legacy.length_m==200
    new=Link(id='new',a={'node':'a'},b={'node':'b'},length_m=20,delay=999)
    assert new.delay==pytest.approx(100e-9)
    new.length_m=100;new.cable_delay()
    assert new.delay==pytest.approx(500e-9)
    assert Link(id='zero',a={'node':'a'},b={'node':'b'},length_m=0).delay==0


def test_fixed_priority_migrates_old_queue_order_and_mapping_together():
    p=Port(index=0,higher_queue_index_first=False,pcp_map=list(range(8)),class_to_queue=list(range(8)),queues=[dict(capacity=i+1) for i in range(8)])
    assert p.higher_queue_index_first is True
    assert p.class_to_queue==list(reversed(range(8)))
    assert [q.capacity for q in p.queues]==list(reversed(range(1,9)))
    assert Port.model_validate(p.model_dump()).model_dump()==p.model_dump()
    s=line();result=compile_scenario(s)
    assert 'reverseOrder = false' not in result['files']['omnetpp.ini']
    assert '.scheduler.reverseOrder = true' in result['files']['omnetpp.ini'] or '.transmissionSelection.reverseOrder = true' in result['files']['omnetpp.ini']


def test_route_dropdown_lists_all_simple_routes_shortest_first_and_paginates():
    s=mesh()
    first=route_options(RouteQuery(scenario=s,flow_id='flow',receiver='receiver',limit=2))
    assert first['has_more']
    second=route_options(RouteQuery(scenario=s,flow_id='flow',receiver='receiver',offset=2,limit=2))
    assert not second['has_more']
    all_paths=first['paths']+second['paths']
    assert {tuple(p) for p in all_paths}=={
        ('sender','a','b','d','receiver'),('sender','a','c','d','receiver'),
        ('sender','a','b','c','d','receiver'),('sender','a','c','b','d','receiver')}
    assert [len(p) for p in all_paths]==sorted(len(p) for p in all_paths)
    resolved=compile_scenario(s)['paths']['flow']['receiver']
    assert first['default_path']==[h['node'] for h in resolved]+['receiver']
    for path in all_paths:
        s.flows[0].routes={'receiver':path}
        compile_scenario(s)


def test_route_choices_respect_vlan_and_flag_unavailable_saved_routes():
    s=mesh();f=s.flows[0]
    f.routes['receiver']=['sender','a','b','d','receiver']
    link=s.links[1] # a-b
    next(n for n in s.nodes if n.id=='a').ports[link.a.port].vlans=[20]
    result=route_options(RouteQuery(scenario=s,flow_id='flow',receiver='receiver'))
    assert result['saved_unavailable'] and result['saved_path'] is None
    assert result['paths']==[['sender','a','c','d','receiver'],['sender','a','c','b','d','receiver']]
    # Block sender's only connection: no routes, rather than a fabricated default.
    s.nodes[0].ports[0].vlans=[20]
    result=route_options(RouteQuery(scenario=s,flow_id='flow',receiver='receiver'))
    assert result['paths']==[] and result['default_path'] is None


def test_route_api_works_for_drafts_without_starting_any_simulation(tmp_path,monkeypatch):
    from designer import runner
    monkeypatch.setattr(runner,'DATA',tmp_path);monkeypatch.setattr(runner,'DB',tmp_path/'routes.sqlite')
    from designer.api import app
    runner.init();client=app.test_client();s=mesh().model_dump()
    def forbidden(*args,**kwargs):raise AssertionError('Route lookup must not start a simulation')
    monkeypatch.setattr(runner,'submit',forbidden)
    response=client.post('/api/routes',json=dict(scenario=s,flow_id='flow',receiver='receiver',limit=2))
    assert response.status_code==200 and len(response.json['paths'])==2
    assert client.post('/api/routes',json=dict(scenario=s,flow_id='flow',receiver='missing')).status_code==422
    assert client.post('/api/routes',json=dict(scenario=s,flow_id='flow',receiver='receiver',limit=0)).status_code==422
