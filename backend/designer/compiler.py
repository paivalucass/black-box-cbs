"""Sample-style, standard INET TSN compiler with explicit loop-free forwarding."""
import hashlib
import json
import math
import re
from collections import defaultdict, deque
from .schema import Scenario, assign_ports

COMPILER_VERSION='3.3'
NED_KEYWORDS=set('package import property module simple network channel moduleinterface channelinterface like extends parameters gates types submodules connections allowunconnected if for input output inout double int bool string object xml volatile const true false this parent typename default'.split())
MEASURE='elapsedTime or queueingTime or processingTime or transmissionTime or propagationTime or delayingTime'

class InvalidScenario(ValueError):
    def __init__(self,diagnostics):
        self.diagnostics=diagnostics
        super().__init__('; '.join(d['message'] for d in diagnostics if d['severity']=='error'))

def resolve(s: Scenario):
    errors=[]
    def error(obj,field,message):errors.append(dict(object=obj,field=field,message=message,severity='error'))
    def check():
        if errors:raise InvalidScenario(errors)
    if s.ned_name in NED_KEYWORDS:error('scenario','ned_name','NED network name is a reserved keyword')
    nodes={n.id:n for n in s.nodes};graph=defaultdict(list);ports={};rates={}
    if len(nodes)<2 or not s.links or not s.flows:error('scenario','topology','Add at least two nodes, a link and a traffic flow')
    for objects,field in [(s.nodes,'nodes'),(s.links,'links'),(s.flows,'flows')]:
        if len({o.id for o in objects})!=len(objects):error('scenario',field,'IDs must be unique')
    for n in s.nodes:
        if n.id in NED_KEYWORDS:error(n.id,'id','Node ID is a reserved NED keyword')
        if n.id in ['configurator','macForwardingTableConfigurator','visualizer','gateScheduleConfigurator','streamRedundancyConfigurator','failureProtectionConfigurator']:error(n.id,'id','Reserved base-network module name')
        if [p.index for p in n.ports]!=list(range(len(n.ports))):error(n.id,'ports','Ports must be contiguous and ordered from zero')
        if n.kind=='device' and len(n.ports)!=1:error(n.id,'ports','End devices need exactly one port')
        if n.clock_drift_ppm is None:error(n.id,'clock_drift_ppm','Conflicting legacy flow clocks: choose one sender-node drift')
        for p in n.ports:
            if any(c<0 or c>=len(p.class_to_queue) for c in p.pcp_map):error(n.id,'pcp_map','Missing traffic class')
            if any(q<0 or q>=len(p.queues) for q in p.class_to_queue):error(n.id,'class_to_queue','Missing physical queue')
            if not p.vlans or any(v<1 or v>4094 for v in p.vlans):error(n.id,'vlans','VLAN IDs must be in 1–4094')
    if s.experiment.warmup>=s.experiment.duration:error('scenario','warmup','Warm-up must be before simulation end')
    used=set();connected_pairs=set()
    for l in sorted(s.links,key=lambda l:l.id):
        pair=tuple(sorted((l.a.node,l.b.node)))
        if pair in connected_pairs:error(l.id,'endpoints','These nodes are already connected')
        connected_pairs.add(pair)
        if l.a.node==l.b.node:error(l.id,'endpoints','Self links are unsupported')
        for ep in [l.a,l.b]:
            k=(ep.node,ep.port)
            if ep.node not in nodes or ep.port>=len(nodes[ep.node].ports):error(l.id,'endpoints','Endpoint does not exist')
            if k in used:error(l.id,'endpoints','Port already connected')
            used.add(k);rates[k]=l.bitrate
        graph[l.a.node].append((l.b.node,l.a.port,l.b.port,l.id))
        graph[l.b.node].append((l.a.node,l.b.port,l.a.port,l.id))
    for n in s.nodes:
        for p in n.ports:
            k=(n.id,p.index);ports[k]=p
            if k not in rates:error(n.id,'ports','Every declared port must be connected');continue
            cbs=[q for q in p.queues if q.shaper=='cbs']
            if any(q.idle_slope>=rates[k] for q in cbs):error(n.id,'idle_slope','CBS idleSlope must be less than port bitrate')
            if sum(q.idle_slope for q in cbs)>rates[k]:error(n.id,'idle_slope','CBS reservations exceed port capacity')
    check()
    seen={next(iter(nodes))};todo=deque(seen)
    while todo:
        for other,*_ in graph[todo.popleft()]:
            if other not in seen:seen.add(other);todo.append(other)
    if seen!=set(nodes):error('scenario','links','All nodes must be connected')
    def shortest(root,target,vlan):
        todo=deque([(root,[root])]);visited={root}
        while todo:
            n,path=todo.popleft()
            if n==target:return path
            if nodes[n].kind=='device' and n!=root:continue
            for other,p,r,_ in graph[n]:
                if other not in visited and vlan in ports[n,p].vlans and vlan in ports[other,r].vlans:
                    visited.add(other);todo.append((other,path+[other]))
        return None
    routes={};addresses={};fdb=defaultdict(set);loads=defaultdict(float);unicast={};macs=set()
    reserved_macs={(f.multicast_mac.lower(),f.vlan) for f in s.flows if f.multicast_mac}
    next_mac=1
    for f in sorted(s.flows,key=lambda f:f.id):
        targets=f.receivers
        if f.source not in nodes or nodes[f.source].kind!='device' or not targets or len(set(targets))!=len(targets) or any(t==f.source or t not in nodes or nodes[t].kind!='device' for t in targets):
            error(f.id,'receivers','Choose distinct existing end-device receivers');continue
        if set(f.routes)-set(targets):error(f.id,'routes','Route override has an unknown receiver')
        multicast=len(targets)>1 or bool(f.multicast_mac)
        addr=f.multicast_mac or targets[0]
        if multicast and not f.multicast_mac:
            while True:
                addr=f'01:00:00:{(next_mac>>16)&255:02x}:{(next_mac>>8)&255:02x}:{next_mac&255:02x}'
                next_mac+=1
                if (addr,f.vlan) not in reserved_macs and (addr,f.vlan) not in macs:break
        if multicast:
            if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',addr) or not int(addr[:2],16)&1 or addr.lower().startswith('01:80:c2') or addr.lower()=='ff:ff:ff:ff:ff:ff':error(f.id,'multicast_mac','Choose a non-reserved multicast MAC')
            if (addr.lower(),f.vlan) in macs:error(f.id,'multicast_mac','Multicast MAC/VLAN must be unique per flow')
            macs.add((addr.lower(),f.vlan))
        addresses[f.id]=addr
        rate=1+nodes[f.source].clock_drift_ppm*1e-6
        first=f.start/rate;cadence=(f.burst_period if f.sending_mode=='burst' else f.interval)/rate
        expected=1 if f.sending_mode=='once' else max(0,math.ceil((s.experiment.duration-first)/cadence))*(f.burst_size if f.sending_mode=='burst' else 1)
        if first>=s.experiment.duration:error(f.id,'start','Start offset must occur before simulation end')
        if expected>2000000:error(f.id,'interval','Limit each flow to two million generated packets')
        edge_set=set();parent={};routes[f.id]={}
        for dest in sorted(targets):
            # Reverse BFS gives consistent destination-rooted unicast forwarding.
            path=f.routes.get(dest)
            if not path:
                path=shortest(f.source,dest,f.vlan) if multicast else shortest(dest,f.source,f.vlan)
                if path and not multicast:path=list(reversed(path))
            if not path or any(n not in nodes for n in path) or path[0]!=f.source or path[-1]!=dest or len(set(path))!=len(path):error(f.id,'routes',f'Invalid or unreachable route to {dest}');continue
            hops=[]
            for a,b in zip(path,path[1:]):
                choices=[e for e in graph[a] if e[0]==b]
                if len(choices)!=1:error(f.id,'routes','Route is missing or ambiguous; parallel links need distinct routing support');break
                _,p,r,_=choices[0]
                if nodes[a].kind=='device' and a!=f.source:error(f.id,'routes','End devices cannot forward traffic')
                if f.vlan not in ports[a,p].vlans or f.vlan not in ports[b,r].vlans:error(f.id,'vlan','VLAN not permitted along selected path')
                if multicast:
                    if b in parent and parent[b]!=a:error(f.id,'routes','Multicast routes must form one source-rooted tree')
                    parent[b]=a
                tc=ports[a,p].pcp_map[f.pcp];q=ports[a,p].class_to_queue[tc]
                hops.append(dict(node=a,port=p,traffic_class=tc,queue=q));edge_set.add((a,p,q))
                if nodes[a].kind=='switch':
                    key=(a,addr,f.vlan)
                    if not multicast and key in unicast and unicast[key]!=p:error(f.id,'routes','Conflicting static unicast routes')
                    if not multicast:unicast[key]=p
                    fdb[a].add((addr,f.vlan,p))
            routes[f.id][dest]=hops
        frequency=1/max(s.experiment.duration-first,1e-12) if f.sending_mode=='once' else (f.burst_size if f.sending_mode=='burst' else 1)/cadence
        for edge in edge_set:loads[edge]+=(max(64,f.payload+22)+20)*8*frequency
    check()
    # Validate the combined unicast forwarding graph, including manual overrides.
    for addr,vlan in {(a,v) for _,a,v in unicast}:
        for root in nodes:
            trail=set();n=root
            while (n,addr,vlan) in unicast:
                if n in trail:error('scenario','routes','Static unicast forwarding cycle');break
                trail.add(n);p=unicast[n,addr,vlan];n=next(e[0] for e in graph[n] if e[1]==p)
    check()
    diagnostics=[]
    for (n,p,q),load in loads.items():
        queue=ports[n,p].queues[q];budget=queue.idle_slope if queue.shaper=='cbs' else rates[n,p]
        if load>budget:diagnostics.append(dict(object=n,field='load',message=f'eth{p} queue {q}: load exceeds service budget',severity='warning'))
    return dict(paths=routes,addresses=addresses,forwarding={n:[dict(address=a,vlan=v,interface=f'eth{p}') for a,v,p in sorted(rows)] for n,rows in fdb.items()},loads=[dict(node=n,port=p,queue=q,bps=v) for (n,p,q),v in sorted(loads.items())],diagnostics=diagnostics)

def compile_scenario(s: Scenario,candidates=None):
    s=s.model_copy(deep=True);assign_ports(s);variables=[];known=set()
    for n in s.nodes:
        for p in n.ports:
            for i,q in enumerate(p.queues):
                if q.shaper=='cbs' and q.optimize:
                    key=f'{n.id}/{p.index}/{i}';known.add(key)
                    if key in (candidates or {}):
                        value=float(candidates[key])
                        if not math.isfinite(value) or value<=0:raise ValueError('Candidate must be finite and positive')
                        q.idle_slope=value
                    variables.append(dict(key=key,idle_slope=q.idle_slope,parameter=f'*.{n.id}.eth[{p.index}].macLayer.queue.transmissionSelectionAlgorithm[{i}].idleSlope'))
    if set(candidates or {})-known:raise ValueError('Unknown or unselected CBS candidate')
    resolved=resolve(s);name=s.ned_name;prefix='black_box_cbs.generated'
    semantic=s.model_dump();semantic.pop('name')
    for n in semantic['nodes']:n.pop('x');n.pop('y')
    digest=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
    ned=[f'package {prefix};','import inet.networks.base.TsnNetworkBase;','import inet.node.tsn.TsnDevice;','import inet.node.tsn.TsnSwitch;','import inet.node.ethernet.*;',f'network {name} extends TsnNetworkBase','{','    parameters:','        double ethernetPropagationDelay @unit(s) = default(0s);','    submodules:']
    for n in sorted(s.nodes,key=lambda n:n.id):ned += [f'        {n.id}: Tsn'+('Device' if n.kind=='device' else 'Switch')+f' {{ numEthInterfaces = {len(n.ports)}; @display("p={n.x},{n.y}"); }}']
    ned.append('    connections:')
    for l in sorted(s.links,key=lambda l:l.id):
        channel={1e8:'Eth100M',1e9:'Eth1G',1e10:'Eth10G'}.get(l.bitrate,'EthernetLink')
        rate='' if channel!='EthernetLink' else f'datarate = {l.bitrate}bps; '
        ned.append(f'        {l.a.node}.ethg[{l.a.port}] <--> {channel} {{ {rate}length = {l.length_m:.15g}m; }} <--> {l.b.node}.ethg[{l.b.port}];')
    ned.append('}')
    sections={k:[] for k in ['GENERAL','BITRATES','ETHERNET STACK','STATIC UNICAST AND MULTICAST FORWARDING','CBS PORTS','STRICT PRIORITY PORTS','APPLICATION CONFIGURATION','NODE CLOCKS','LINK PACKET OBSERVATIONS','MEASUREMENTS']}
    sections['GENERAL']=[f'network = {prefix}.{name}','user-interface = Cmdenv',f'sim-time-limit = {s.experiment.duration}s','simtime-resolution = fs',f'seed-set = {s.experiment.seed}','cmdenv-express-mode = true','cmdenv-status-frequency = 1s',f'description = {json.dumps(s.name)}','*.configurator.typename = ""','*.macForwardingTableConfigurator.typename = ""','**.hasTimeSynchronization = false','**.eth[*].phyLayer.transmitter.typename = "StreamingTransmitter"','**.eth[*].phyLayer.receiver.typename = "DestreamingReceiver"','# INET default IFG: 96 bits / interface bitrate. No additional switch/PHY delays.']
    for n in sorted(s.nodes,key=lambda n:n.id):
        b=f'*.{n.id}'
        sections['ETHERNET STACK'] += [f'{b}.hasEgressTrafficShaping = {str(any(q.shaper=="cbs" for p in n.ports for q in p.queues)).lower()}',f'{b}.hasIncomingStreams = true',f'{b}.hasOutgoingStreams = true']
        if n.kind=='device':sections['ETHERNET STACK'] += [f'{b}.hasBridging = false',f'{b}.hasIpv4 = false',f'{b}.hasTcp = false',f'{b}.hasUdp = false',f'{b}.numLoInterfaces = 0',f'{b}.ethernet.hasSocketSupport = true',f'{b}.ieee8021q.typename = "Ieee8021qProtocol"']
        else:
            sections['ETHERNET STACK'] += [f'{b}.bridging.vlanPolicy.typename = "VlanPolicyLayer"']
            allowed={f'eth{p.index}':p.vlans for p in n.ports}
            sections['ETHERNET STACK'] += [f'{b}.bridging.vlanPolicy.{side}Filter.acceptedVlanIds = {json.dumps(allowed)}' for side in ['inbound','outbound']]
            sections['STATIC UNICAST AND MULTICAST FORWARDING'].append(f'{b}.macTable.forwardingTable = '+json.dumps(resolved['forwarding'].get(n.id,[])))
        sections['NODE CLOCKS'] += [f'{b}.clock.typename = "SettableClock"',f'{b}.clock.oscillator.typename = "ConstantDriftOscillator"',f'{b}.clock.oscillator.nominalTickLength = 10ns',f'{b}.clock.oscillator.driftRate = {n.clock_drift_ppm}ppm']
        for p in n.ports:
            link=next(l for l in s.links if (l.a.node,l.a.port)==(n.id,p.index) or (l.b.node,l.b.port)==(n.id,p.index))
            qbase=f'{b}.eth[{p.index}].macLayer.queue';cbs=any(q.shaper=='cbs' for q in p.queues)
            sections['BITRATES'].append(f'{b}.eth[{p.index}].bitrate = {link.bitrate}bps')
            lines=sections['CBS PORTS' if cbs else 'STRICT PRIORITY PORTS']
            lines += [f'# PCP -> class {p.pcp_map}; class -> queue {p.class_to_queue}',f'{qbase}.typename = "'+('Ieee8021qTimeAwareShaper' if cbs else 'PriorityQueue')+'"',f'{qbase}.'+('numTrafficClasses' if cbs else 'numQueues')+f' = {len(p.queues)}',f'{qbase}.classifier.typename = "PcpClassifier"',f'{qbase}.classifier.mode = "both"',f'{qbase}.classifier.pcpToGateIndex = '+json.dumps([p.class_to_queue[c] for c in p.pcp_map]),f'{qbase}.'+('transmissionSelection' if cbs else 'scheduler')+'.reverseOrder = true']
            for i,q in enumerate(p.queues):
                lines += [f'{qbase}.queue[{i}].packetCapacity = {q.capacity}',f'{qbase}.queue[{i}].dropperClass = "inet::queueing::PacketAtCollectionEndDropper"']
                if cbs:lines += [f'{qbase}.transmissionGate[{i}].initiallyOpen = true',f'{qbase}.transmissionGate[{i}].durations = []']
                if q.shaper=='cbs':lines += [f'{qbase}.transmissionSelectionAlgorithm[{i}].typename = "Ieee8021qCreditBasedShaper"',f'{qbase}.transmissionSelectionAlgorithm[{i}].idleSlope = {q.idle_slope}bps']
    directions=[]
    if s.experiment.record_link_occupancy:
        for link in sorted(s.links,key=lambda link:link.id):
            for sender,receiver in [(link.a,link.b),(link.b,link.a)]:
                base=f'*.{sender.node}.eth[{sender.port}].phyLayer.cutthroughSink'
                filename=f'link-events-{sender.node}-{sender.port}.json'
                sections['LINK PACKET OBSERVATIONS'] += [
                    f'{base}.typename = "FlowMeasurementRecorder"',
                    f'{base}.measure = "packetEvent"',
                    f'{base}.flowName = "__link_observer__"',
                    f'{base}.endMeasurement = true',
                    f'{base}.packetEventFileName = "${{resultdir}}/{filename}"',
                    f'{base}.**.vector-recording = false',
                    f'{base}.**.scalar-recording = false']
                flows=sorted({f for f,paths in resolved['paths'].items() for hops in paths.values()
                              if any(h['node']==sender.node and h['port']==sender.port for h in hops)})
                directions.append(dict(key=f'{link.id}:{sender.node}',link_id=link.id,source=sender.node,
                    target=receiver.node,port=sender.port,flow_ids=flows,event_file=filename,
                    observer=f'{name}.{sender.node}.eth[{sender.port}].phyLayer.cutthroughSink',
                    transmitter=f'{name}.{sender.node}.eth[{sender.port}].phyLayer.transmitter',
                    queue=f'{name}.{sender.node}.eth[{sender.port}].macLayer.queue'))
    apps=defaultdict(list);flowmap={}
    for f in sorted(s.flows,key=lambda f:f.id):
        i=len(apps[f.source]);apps[f.source].append((f,None))
        flowmap[f.id]=dict(source=f'{name}.{f.source}.app[{i}].source',receivers={})
        for dest in sorted(f.receivers):
            i=len(apps[dest]);apps[dest].append((f,dest))
            flowmap[f.id]['receivers'][dest]=dict(recorder=f'{name}.{dest}.app[{i}].measurementRecorder',flow=f.id,sink=f'{name}.{dest}.app[{i}].sink')
    for node,items in sorted(apps.items()):
        lines=sections['APPLICATION CONFIGURATION'];lines.append(f'*.{node}.numApps = {len(items)}')
        for i,(f,dest) in enumerate(items):
            b=f'*.{node}.app[{i}]';addr=resolved['addresses'][f.id]
            lines += [f'{b}.typename = "Ethernet'+('SinkApp' if dest else 'SourceApp')+'"',f'{b}.display-name = "{f.id}'+('_sink' if dest else '')+'"',f'{b}.io.interface = "eth0"']
            if dest:
                # Native recorder filters per-flow packet names. Socket payload still
                # contains the VLAN header; measurements track tagged payload regions.
                lines += [f'{b}.io.localAddress = "{addr}"',f'{b}.io.remoteAddress = "{f.source}"',f'{b}.io.protocol = "ieee8021qctag"',f'{b}.io.steal = true',f'{b}.measurementRecorder.typename = "FlowMeasurementRecorder"',f'{b}.measurementRecorder.packetFilter = "{f.id}-*"',f'{b}.measurementRecorder.flowName = "{f.id}"',f'{b}.measurementRecorder.measure = "{MEASURE}"']
            else:
                gap=f.burst_period if f.sending_mode=='burst' else f.interval
                interval='inf s' if f.sending_mode=='once' else f'(seq("{f.id}") % {f.burst_size} == {f.burst_size-1} ? {gap}s : 0s)' if f.sending_mode=='burst' else f'{gap}s'
                lines += [f'{b}.io.remoteAddress = "{addr}"',f'{b}.source.packetNameFormat = "{f.id}-%c"',f'{b}.source.packetLength = {f.payload}B',f'{b}.source.productionInterval = {interval}',f'{b}.source.initialProductionOffset = {f.start}s',f'{b}.source.clockModule = "^.^.clock"',f'{b}.tagger.typename = "PacketTagger"',f'{b}.tagger.encapsulationProtocols = ["ieee8021qctag"]',f'{b}.tagger.vlanId = {f.vlan}',f'{b}.tagger.pcp = {f.pcp}',f'{b}.measurementStarter.typename = "FlowMeasurementStarter"',f'{b}.measurementStarter.flowName = "{f.id}"',f'{b}.measurementStarter.measure = "{MEASURE + (" or packetEvent" if s.experiment.record_link_occupancy else "")}"']
    sections['MEASUREMENTS']=['**.vector-recording = true','**.scalar-recording = true','**.measurementRecorder.bitElapsedTimePerRegion.result-recording-modes = +vector','**.busy.result-recording-modes = +vector','**.macLayer.queue.queue[*].queueLength:vector.vector-recording = true']
    for stat in ['queueingTime','queueBitLength','incomingPacketLengths','outgoingPacketLengths','dataRate','incomingDataRate','outgoingDataRate','gateState','guardBandState']:
        sections['MEASUREMENTS'].append(f'**.{stat}:vector.vector-recording = false')
    ini=['[General]']
    for title,lines in sections.items():ini += ['', '# '+ '='*65,'# '+title,'# '+ '='*65,*lines]
    manifest=dict(compiler_version=COMPILER_VERSION,measurement_version=3,generation_horizon='simulation_end',link_occupancy=dict(version=1,enabled=s.experiment.record_link_occupancy,directions=directions),semantic_hash=digest,flow_modules=flowmap,variables=variables,candidates=candidates or {},network=f'{prefix}.{name}',dependencies={'omnetpp':'6.4.0','inet':'4.7.0'})
    files={'ned/package.ned':f'package {prefix};\n',f'ned/{name}.ned':'\n'.join(ned)+'\n','omnetpp.ini':'\n'.join(ini)+'\n','scenario.json':s.model_dump_json(indent=2),'resolved.json':json.dumps(resolved,indent=2)}
    files['run.sh']='#!/usr/bin/env bash\nset -euo pipefail\ncd "$(dirname "$0")"\n: "${INET_ROOT:?Enter an opp_env shell with INET first}"\n: "${OMNETPP_ROOT:?Enter an opp_env shell with OMNeT++ first}"\nexcludes=$(paste -sd \';\' "$INET_ROOT/.nedexclusions")\nexec "$OMNETPP_ROOT/bin/opp_run" -u Cmdenv -l "$INET_ROOT/src/INET" -n "$INET_ROOT/src:$PWD/ned" -x "$excludes" -f omnetpp.ini --result-dir="$PWD/results"\n'
    manifest['file_hashes']={k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}
    files['manifest.json']=json.dumps(manifest,indent=2)
    return dict(files=files,manifest=manifest,**resolved)
