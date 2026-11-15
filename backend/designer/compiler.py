"""Deterministic INET 4.7 compiler for tagged, acyclic Ethernet TSN networks."""
import hashlib
import json
from collections import defaultdict, deque
from .schema import Scenario

class InvalidScenario(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('; '.join(d['message'] for d in diagnostics if d['severity'] == 'error'))

def resolve(s: Scenario):
    issues = []
    def report(obj, field, message, severity='error'):
        issues.append(dict(object=obj, field=field, message=message, severity=severity))
    nodes = {n.id: n for n in s.nodes}
    for items, label in [(s.nodes,'nodes'), (s.links,'links'), (s.flows,'flows')]:
        if len({i.id for i in items}) != len(items): report('scenario',label,'IDs must be unique')
    reserved = {'configurator', 'macForwardingTableConfigurator', 'Scenario'}
    graph = defaultdict(list)
    used = set()
    rates = {}
    for n in s.nodes:
        indices = [p.index for p in n.ports]
        if n.id in reserved: report(n.id,'id','Reserved module name')
        if sorted(indices) != list(range(len(indices))): report(n.id,'ports','Ports must have contiguous unique indices starting at zero')
        if n.kind == 'device' and len(n.ports) != 1: report(n.id,'ports','Initial tagged UDP profile supports one port per end device')
        for p in n.ports:
            if not p.vlans or any(v < 1 or v > 4094 for v in p.vlans): report(n.id,'vlans','Specify permitted VLANs in 1–4094')
            if any(c < 0 or c >= len(p.queues) for c in p.pcp_map): report(n.id,'pcp_map','PCP mapping refers to a missing class')
    for link in s.links:
        ends = [link.a,link.b]
        if link.a.node == link.b.node: report(link.id,'endpoints','Self links are unsupported')
        for ep in ends:
            key = (ep.node,ep.port)
            if ep.node not in nodes or ep.port not in [p.index for p in nodes[ep.node].ports]:
                report(link.id,'endpoints','Endpoint does not exist'); continue
            if key in used: report(link.id,'endpoints','Port already connected')
            used.add(key); rates[key] = link.bitrate
        graph[link.a.node].append((link.b.node,link.a.port,link))
        graph[link.b.node].append((link.a.node,link.b.port,link))
    seen = set()
    def visit(node, parent):
        seen.add(node)
        for other,_,link in graph[node]:
            if link.id == parent: continue
            if other in seen: return False
            if not visit(other,link.id): return False
        return True
    if nodes and not visit(next(iter(nodes)),None): report('scenario','links','Layer 2 cycles require explicit forwarding and are unsupported')
    if seen != set(nodes): report('scenario','links','All nodes must be connected')
    for n in s.nodes:
        for p in n.ports:
            key = (n.id,p.index)
            if key not in used: report(n.id,'ports','Every declared port must be connected'); continue
            cbs = [q for q in p.queues if q.shaper == 'cbs']
            if any(q.idle_slope >= rates[key] for q in cbs): report(n.id,'idle_slope','CBS idleSlope must be less than port bitrate')
            if sum(q.idle_slope for q in cbs) > rates[key]: report(n.id,'idle_slope','CBS reservations exceed port capacity')
    if any(i['severity']=='error' for i in issues): raise InvalidScenario(issues)
    paths = {}; loads = defaultdict(float)
    for f in s.flows:
        if f.source not in nodes or f.destination not in nodes or f.source == f.destination:
            report(f.id,'endpoints','Choose distinct existing source and destination'); continue
        if any(nodes[e].kind != 'device' for e in [f.source,f.destination]): report(f.id,'endpoints','Flows must terminate on end devices')
        if (f.stop-f.start)/f.interval > 2000000: report(f.id,'interval','Limit each flow to two million generated packets per evaluation')
        if not s.experiment.warmup < f.stop or f.start >= f.stop: report(f.id,'stop','Traffic stop must follow start and warm-up')
        if f.stop + f.deadline > s.experiment.duration: report(f.id,'deadline','End time must allow all traffic deadlines to mature')
        todo = deque([(f.source,[])]); walked = {f.source}; path = None
        while todo:
            node,hops = todo.popleft()
            if node == f.destination: path=hops; break
            for other,port,link in graph[node]:
                if other not in walked:
                    walked.add(other); todo.append((other,hops+[(node,port,other,link)]))
        if path is None: report(f.id,'destination','No path'); continue
        paths[f.id] = []
        for node,port,other,link in path:
            reverse = link.a if link.a.node == other else link.b
            if nodes[node].kind == 'device' and node != f.source: report(f.id,'path','An end device cannot forward a flow')
            for name,index in [(node,port),(other,reverse.port)]:
                if name in nodes and index < len(nodes[name].ports) and f.vlan not in nodes[name].ports[index].vlans:
                    report(f.id,'vlan',f'VLAN {f.vlan} is not permitted on {name}:eth{index}')
            if port >= len(nodes[node].ports): continue
            tc = nodes[node].ports[port].pcp_map[f.pcp]
            paths[f.id].append(dict(node=node,port=port,traffic_class=tc))
            loads[(node,port,tc)] += max(64,f.payload+46)*8/f.interval + 20*8/f.interval
    for (node,port,tc),load in loads.items():
        if (node,port) not in rates: continue
        q = nodes[node].ports[port].queues
        if tc >= len(q): continue
        cap = q[tc].idle_slope if q[tc].shaper == 'cbs' else rates[(node,port)]
        if load > cap: report(node,'load',f'eth{port} class {tc}: estimated wire load {load/1e6:.2f} Mbps exceeds service budget {cap/1e6:.2f} Mbps','warning')
    if any(i['severity']=='error' for i in issues): raise InvalidScenario(issues)
    return dict(paths=paths, diagnostics=issues, loads=[dict(node=n,port=p,traffic_class=t,bps=v) for (n,p,t),v in sorted(loads.items())])

def compile_scenario(s: Scenario, candidates=None):
    s = s.model_copy(deep=True)
    candidates = candidates or {}
    known = set()
    for n in s.nodes:
        for p in n.ports:
            for i,q in enumerate(p.queues):
                key=f'{n.id}/{p.index}/{i}'
                if q.shaper == 'cbs' and q.optimize:
                    known.add(key)
                    if key in candidates:
                        value=float(candidates[key])
                        if not 0 < value < float('inf'): raise ValueError('Candidate must be finite and positive')
                        q.idle_slope=value
    if set(candidates)-known: raise ValueError('Candidate contains an unknown or unselected CBS variable')
    resolved = resolve(s)
    canonical = s.model_dump()
    semantic = s.model_dump()
    for n in semantic['nodes']: n.pop('x'); n.pop('y')
    semantic.pop('name')
    digest = hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
    ned=['package black_box_cbs.generated;','import inet.node.tsn.TsnDevice;','import inet.node.tsn.TsnSwitch;','import inet.node.ethernet.EthernetLink;','import inet.networklayer.configurator.ipv4.Ipv4NetworkConfigurator;','import inet.linklayer.configurator.common.MacForwardingTableConfigurator;','network Scenario {','    submodules:','        configurator: Ipv4NetworkConfigurator;','        macForwardingTableConfigurator: MacForwardingTableConfigurator;']
    ini=['[General]','network = black_box_cbs.generated.Scenario','user-interface = Cmdenv',f'sim-time-limit = {s.experiment.duration}s',f'seed-set = {s.experiment.seed}','cmdenv-express-mode = true','cmdenv-status-frequency = 1s','**.vector-recording = true','**.scalar-recording = true','**.ipv4.arp.typename = "GlobalArp"','# CBS uses INET default sendSlope and credit bounds (unbounded by default).']
    for n in sorted(s.nodes,key=lambda n:n.id):
        ned.append(f'        {n.id}: Tsn'+('Device' if n.kind=='device' else 'Switch')+f' {{ numEthInterfaces = {len(n.ports)}; }}')
        prefix=f'*.{n.id}'
        ini += [f'{prefix}.hasOutgoingStreams = true',f'{prefix}.hasIncomingStreams = true',f'{prefix}.hasEgressTrafficShaping = true',f'{prefix}.bridging.vlanPolicy.typename = "VlanPolicyLayer"']
        allowed = {f'eth{p.index}':p.vlans for p in n.ports}
        ini += [f'{prefix}.bridging.vlanPolicy.{side}Filter.acceptedVlanIds = {json.dumps(allowed)}' for side in ['inbound','outbound']]
        for p in n.ports:
            rate=next(l.bitrate for l in s.links if (l.a.node,l.a.port)==(n.id,p.index) or (l.b.node,l.b.port)==(n.id,p.index))
            base=f'{prefix}.eth[{p.index}]'
            ini += [f'{base}.bitrate = {rate}bps',f'{base}.macLayer.queue.numTrafficClasses = {len(p.queues)}']
            matrix=[[c]*8 for c in p.pcp_map]
            ini.append(f'{base}.macLayer.queue.classifier.mapping = {json.dumps(matrix)}')
            for i,q in enumerate(p.queues):
                qb=f'{base}.macLayer.queue'
                ini += [f'{qb}.queue[{i}].packetCapacity = {q.capacity}',f'{qb}.queue[{i}].dropperClass = "inet::queueing::PacketAtCollectionEndDropper"']
                if q.shaper=='cbs': ini += [f'{qb}.transmissionSelectionAlgorithm[{i}].typename = "Ieee8021qCreditBasedShaper"',f'{qb}.transmissionSelectionAlgorithm[{i}].idleSlope = {q.idle_slope}bps']
    # Explicit VLAN-aware forwarding avoids relying on the configurator's VID-0 entries.
    tables=defaultdict(dict)
    for f in s.flows:
        for hop in resolved['paths'][f.id]:
            if next(n for n in s.nodes if n.id==hop['node']).kind=='switch':
                tables[hop['node']][(f.destination,f.vlan)]=dict(address=f.destination,vlan=f.vlan,interface=f'eth{hop["port"]}')
    for node,table in sorted(tables.items()):
        ini.append(f'*.{node}.macTable.forwardingTable = {json.dumps(list(table.values()))}')
    apps=defaultdict(list); flowmap={}
    for ix,f in enumerate(sorted(s.flows,key=lambda f:f.id)):
        tx=len(apps[f.source]); apps[f.source].append((f,True,10000+ix))
        rx=len(apps[f.destination]); apps[f.destination].append((f,False,10000+ix))
        flowmap[f.id]=dict(source=f'Scenario.{f.source}.app[{tx}].source',sink=f'Scenario.{f.destination}.app[{rx}].sink')
    for node,entries in apps.items():
        ini.append(f'*.{node}.numApps = {len(entries)}'); identify=[]; encode=[]
        for i,(f,send,port) in enumerate(entries):
            b=f'*.{node}.app[{i}]'
            ini.append(f'{b}.typename = "Udp'+('SourceApp' if send else 'SinkApp')+'"')
            if send:
                ini += [f'{b}.io.destAddress = "{f.destination}"',f'{b}.io.destPort = {port}',f'{b}.source.packetNameFormat = "{f.id}-%c"',f'{b}.source.packetLength = {f.payload}B',f'{b}.source.initialProductionOffset = {f.start}s',f'{b}.source.productionInterval = simTime() + {f.interval}s < {f.stop}s ? {f.interval}s : inf s']
                identify.append('{stream: "'+f.id+'", packetFilter: expr(udp.destPort == '+str(port)+')}')
                encode.append(dict(stream=f.id,vlan=f.vlan,pcp=f.pcp))
            else: ini.append(f'{b}.io.localPort = {port}')
        if identify:
            ini += [f'*.{node}.bridging.streamIdentifier.identifier.mapping = ['+', '.join(identify)+']',f'*.{node}.bridging.streamCoder.encoder.mapping = {json.dumps(encode)}']
    ned.append('    connections:')
    for l in sorted(s.links,key=lambda l:l.id):
        ned.append(f'        {l.a.node}.ethg[{l.a.port}] <--> EthernetLink {{ datarate = {l.bitrate}bps; delay = {l.delay}s; }} <--> {l.b.node}.ethg[{l.b.port}];')
    ned.append('}')
    manifest=dict(compiler_version='1.0',semantic_hash=digest,flow_modules=flowmap,candidates=candidates,measurement_version=1,variables=[dict(key=f'{n.id}/{p.index}/{i}',idle_slope=q.idle_slope) for n in s.nodes for p in n.ports for i,q in enumerate(p.queues) if q.shaper=='cbs' and q.optimize])
    files={'ned/package.ned':'package black_box_cbs.generated;\n','ned/Scenario.ned':'\n'.join(ned)+'\n','omnetpp.ini':'\n'.join(ini)+'\n','scenario.json':json.dumps(canonical,indent=2),'resolved.json':json.dumps(resolved,indent=2),'manifest.json':json.dumps(manifest,indent=2)}
    files['run.sh'] = '\n'.join([
        '#!/usr/bin/env bash', 'set -euo pipefail',
        'cd "$(dirname "$0")"',
        ': "${OMNETPP_ROOT:?Run inside the configured opp_env shell}"',
        ': "${INET_ROOT:?Run inside the configured INET environment}"',
        'exclusions="$(tr \'\\n\' \';\' < "$INET_ROOT/.nedexclusions")"',
        'exec "$OMNETPP_ROOT/bin/opp_run" -u Cmdenv -l "$INET_ROOT/src/INET" -n "$INET_ROOT/src:$PWD/ned" -x "$exclusions" -f "$PWD/omnetpp.ini" --result-dir="$PWD/results"',
        ''
    ])
    manifest['dependencies']={'omnetpp':'6.4.0','inet':'4.7.0'}
    manifest['file_hashes']={name:hashlib.sha256(content.encode()).hexdigest() for name,content in files.items() if name!='manifest.json'}
    files['manifest.json']=json.dumps(manifest,indent=2)
    return dict(files=files,**resolved,manifest=manifest)
