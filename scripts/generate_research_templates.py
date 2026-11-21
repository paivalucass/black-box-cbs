#!/usr/bin/env python3
"""Generate reviewed, deterministic TSN research networks using the shared schema.

Run from the repository root: python3 scripts/generate_research_templates.py
Use --check to verify the committed JSON without writing it.
"""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from designer.schema import Scenario, Queue
from designer.compiler import compile_scenario

VLANS = [10, 20, 30, 40]
ROLES = ['camera', 'lidar', 'radar', 'controller', 'actuator', 'compute', 'logger', 'gateway']


@dataclass(frozen=True)
class Profile:
    filename: str
    name: str
    network: str
    groups: int
    topology: str
    duration: float
    warmup: float
    seed: int
    purpose: str


PROFILES = [
    Profile('01-automotive-zonal.json', 'Automotive zonal mesh', 'AutomotiveZonalMesh', 6, 'hierarchy2', .16, .02, 2401,
            'Six vehicle zones, two core switches and four distribution switches. Camera/lidar bursts compete with safety commands and telemetry across VLAN-selected uplinks.'),
    Profile('02-industrial-production.json', 'Industrial production network', 'IndustrialProduction', 7, 'hierarchy3', .20, .02, 2402,
            'Seven production cells on a three-core/four-distribution backbone. Closed-loop control, sensor fusion, multicast heartbeats and maintenance bursts exercise multiple queue classes.'),
    Profile('03-backbone-ring.json', 'Redundant backbone ring', 'RedundantBackboneRing', 8, 'ring', .18, .02, 2403,
            'Eight access sites, eight backbone switches, a physical ring and four cross-ring chords. VLAN-selected access uplinks and static forwarding distribute cross-site traffic.'),
    Profile('04-leaf-spine.json', 'Leaf-spine compute fabric', 'LeafSpineCompute', 12, 'fabric', .12, .012, 2404,
            'Twelve leaves and four spines connect 96 end devices. Each VLAN uses its own spine so all four spines carry traffic; compute fan-in and multicast add contention.'),
    Profile('05-cascaded-bottlenecks.json', 'Cascaded CBS bottlenecks', 'CascadedCbsBottlenecks', 6, 'cascade', .20, .02, 2405,
            'Six access cells feed six transit switches. Two 100 Mb/s transit bottlenecks among 1 Gb/s trunks deliberately overload reservations and exercise finite-buffer loss and long queue waits.'),
]


class Builder:
    def __init__(self, profile, seed):
        self.profile = profile
        self.rng = random.Random(seed)
        self.seed = seed
        self.nodes = []
        self.links = []
        self.flows = []
        self.membership = {}
        self.pairs = set()

    def node(self, name, kind, x, y):
        self.nodes.append(dict(id=name, kind=kind, ports=[], x=round(x,3), y=round(y,3),
                               clock_drift_ppm=self.rng.choice([-55, -35, -20, -8, 0, 12, 25, 40, 60])))

    def link(self, a, b, bitrate=1e9, vlans=VLANS, length=None):
        pair = tuple(sorted((a, b)))
        if pair in self.pairs:
            return
        self.pairs.add(pair)
        name = f'cable{len(self.links):03d}'
        if length is None:
            length = self.rng.choice([3, 5, 8, 12, 18]) if self.profile.topology == 'hierarchy2' else self.rng.choice([5, 10, 20, 35, 50, 80])
        self.links.append(dict(id=name, a=dict(node=a), b=dict(node=b), bitrate=bitrate, length_m=length))
        self.membership[name] = list(vlans)

    def flow(self, name, source, receivers, pcp, vlan, payload, interval, deadline,
             burst=1, burst_period=None, start=None):
        receivers = list(dict.fromkeys(r for r in receivers if r != source))
        if not receivers:
            raise ValueError(f'{name}: no distinct receiver')
        self.flows.append(dict(id=name, source=source, receivers=receivers, pcp=pcp, vlan=vlan,
                               payload=payload, interval=interval, deadline=deadline,
                               sending_mode='burst' if burst > 1 else 'periodic',
                               burst_size=burst, burst_period=burst_period or interval,
                               start=self.rng.choice([0, .00025, .0005, .001, .002]) if start is None else start))

    def host(self, group, role):
        return f'Z{group:02d}_{role}'

    def target(self, group, role, source):
        target = self.host(group % self.profile.groups, role)
        return self.host((group + 1) % self.profile.groups, role) if target == source else target

    def build_topology(self):
        p = self.profile
        access = [f'Access{i:02d}' for i in range(p.groups)]
        # Four-column device clusters keep labels separated when the canvas is zoomed.
        centres = [(650 + (i % 3) * 1250, 1000 + (i // 3) * 850) for i in range(p.groups)]
        if p.topology.startswith('hierarchy'):
            cores = 2 if p.topology == 'hierarchy2' else 3
            for i in range(cores):
                self.node(f'Core{i:02d}', 'switch', 1250 + i * 650, 100)
            for i in range(4):
                self.node(f'Dist{i:02d}', 'switch', 550 + i * 850, 450)
                for side in range(2):
                    vlans = [v for j, v in enumerate(VLANS) if (j // 2 if cores == 2 else (j // 2 + i) % 2) == side]
                    core = (i // 2 + side) % cores if cores == 2 else (i + side) % cores
                    self.link(f'Dist{i:02d}', f'Core{core:02d}', bitrate=10e9, vlans=vlans)
            for i in range(cores):
                self.link(f'Core{i:02d}', f'Core{(i + 1) % cores:02d}', bitrate=10e9)
            for i, name in enumerate(access):
                self.node(name, 'switch', *centres[i])
                for side in range(2):
                    vlans = [v for j, v in enumerate(VLANS) if (j + i) % 2 == side]
                    self.link(name, f'Dist{(i + side) % 4:02d}', vlans=vlans)
        elif p.topology == 'ring':
            for i in range(8):
                self.node(f'Backbone{i:02d}', 'switch', 1900 + 1550 * math.cos(i * math.pi / 4), 300 + 250 * math.sin(i * math.pi / 4))
            for i in range(8):
                self.link(f'Backbone{i:02d}', f'Backbone{(i + 1) % 8:02d}', bitrate=1e9)
            for i in range(4):
                self.link(f'Backbone{i:02d}', f'Backbone{i + 4:02d}', bitrate=1e9)
            for i, name in enumerate(access):
                self.node(name, 'switch', *centres[i])
                for side in range(2):
                    vlans = [v for j, v in enumerate(VLANS) if (j + i) % 2 == side]
                    self.link(name, f'Backbone{(i + side) % 8:02d}', vlans=vlans)
        elif p.topology == 'fabric':
            for i in range(4):
                self.node(f'Spine{i:02d}', 'switch', 550 + i * 850, 200)
            for i, name in enumerate(access):
                self.node(name, 'switch', *centres[i])
                for spine, vlan in enumerate(VLANS):
                    self.link(name, f'Spine{spine:02d}', bitrate=1e9, vlans=[vlan], length=self.rng.choice([5, 10, 15, 25, 35]))
        elif p.topology == 'cascade':
            for i in range(p.groups):
                self.node(f'Transit{i:02d}', 'switch', 400 + i * 650, 300)
                self.node(access[i], 'switch', *centres[i])
                self.link(access[i], f'Transit{i:02d}')
            for i, rate in enumerate([1e9, 100e6, 1e9, 100e6, 1e9]):
                self.link(f'Transit{i:02d}', f'Transit{i + 1:02d}', bitrate=rate)
        for i, (cx, cy) in enumerate(centres):
            for j, role in enumerate(ROLES):
                name = self.host(i, role)
                self.node(name, 'device', cx + (j % 4 - 1.5) * 270, cy + 180 + (j // 4) * 180)
                rate = 1e9 if role in ['camera', 'lidar', 'compute', 'logger'] else 100e6
                self.link(name, access[i], bitrate=rate,
                          length=self.rng.choice([1, 2, 3, 5]) if p.topology == 'hierarchy2' else self.rng.choice([2, 5, 8, 12, 20]))

    def build_traffic(self):
        count = self.profile.groups
        stress = self.profile.topology == 'cascade'
        camera_period = .008 if stress else .004
        camera_burst = 12 if stress else 24
        lidar_period = .020 if stress else .005
        fanin_period = .040 if stress else .010
        fanin_burst = 24 if stress else round(450e6 / count * fanin_period / (1400 * 8))
        for g in range(count):
            for r, role in enumerate(ROLES):
                source = self.host(g, role)
                next_compute = self.target(g + 1, 'compute', source)
                opposite_compute = self.target(g + count // 2, 'compute', source)
                if role == 'camera':
                    receivers = [next_compute, self.host(g, 'compute'), opposite_compute] if g % 2 == 0 else [next_compute]
                    self.flow(f'camera_feed_{g:02d}', source, receivers, 4, 30, 1200, camera_period, .020, burst=camera_burst, burst_period=camera_period, start=(g % 3) * .001)
                elif role == 'lidar':
                    receivers = [opposite_compute, next_compute] if g % 3 == 0 else [opposite_compute]
                    self.flow(f'lidar_cloud_{g:02d}', source, receivers, 5, 30, 1400, lidar_period, .020, burst=20, burst_period=lidar_period, start=(g % 2) * .002)
                elif role == 'radar':
                    self.flow(f'radar_targets_{g:02d}', source, [self.host((g + 1) % count, 'controller')], 6, 20, 400, .002, .005)
                elif role == 'controller':
                    self.flow(f'safety_command_{g:02d}', source, [self.host(g, 'actuator'), self.host((g + 1) % count, 'actuator')], 7, 10, 80, .0005, .002)
                elif role == 'actuator':
                    self.flow(f'actuator_state_{g:02d}', source, [next_compute], 7, 10, 128, .001, .003)
                elif role == 'compute':
                    self.flow(f'control_decision_{g:02d}', source, [self.host((g + 1) % count, 'controller')], 7, 10, 256, .002, .004)
                elif role == 'logger':
                    self.flow(f'log_replication_{g:02d}', source, [self.host((g + count // 2) % count, 'gateway')], 1, 40, 1000, .040, .050, burst=8, burst_period=.040)
                else:
                    self.flow(f'gateway_services_{g:02d}', source, [next_compute], 3, 20, 512, .010, .020)
                # Every node generates independent status, diagnostic and heartbeat streams.
                self.flow(f'status_{g:02d}_{role}', source, [self.target(g + 2, 'compute', source)],
                          2 + r % 2, 20, self.rng.choice([96, 128, 192, 256]), self.rng.choice([.004, .005, .008, .010]), .012)
                self.flow(f'diagnostics_{g:02d}_{role}', source, [self.target(g + r + 2, 'logger', source)],
                          r % 2, 40, self.rng.choice([600, 1000, 1400]), .040, .050,
                          burst=self.rng.choice([4, 6, 8]), burst_period=self.rng.choice([.025, .040, .050]), start=.010 + (r % 4) * .001)
                heartbeat_receivers = [self.target(g + 1, 'controller', source)]
                if r % 4 == 0:
                    heartbeat_receivers += [self.target(g + 2, 'controller', source), self.target(g + 3, 'controller', source)]
                self.flow(f'heartbeat_{g:02d}_{role}', source, heartbeat_receivers, 6, 10, 96, .002, .005)
            # A common burst phase creates visible fan-in on the collector's egress.
            self.flow(f'bulk_fanin_{g:02d}', self.host(g, 'gateway'), [self.host(0, 'compute')],
                      4, 30, 1400, fanin_period, .030, burst=fanin_burst,
                      burst_period=fanin_period, start=.040)

    def scenario(self):
        self.build_topology()
        self.build_traffic()
        s = Scenario.model_validate(dict(name=self.profile.name, ned_name=self.profile.network,
            nodes=self.nodes, links=self.links, flows=self.flows,
            experiment=dict(duration=self.profile.duration, warmup=self.profile.warmup,
                            seed=self.seed, record_link_occupancy=True)))
        nodes = {n.id: n for n in s.nodes}
        for link in s.links:
            for endpoint in [link.a, link.b]:
                node = nodes[endpoint.node]
                port = node.ports[endpoint.port]
                port.vlans = self.membership[link.id]
                port.pcp_map = list(range(8))
                port.class_to_queue = list(range(8))
                capacities = [48, 96, 128, 160, 256, 256, 160, 128]
                fractions = {4: .65, 5: .20, 6: .10}
                if self.profile.topology == 'cascade' and link.bitrate == 100e6 and node.kind == 'switch':
                    fractions = {4: .10, 5: .12, 6: .10}
                    capacities = [24, 48, 80, 96, 96, 128, 96, 128]
                port.queues = [Queue(capacity=capacities[q], shaper='cbs' if node.kind == 'switch' and q in fractions else 'sp',
                                     idle_slope=link.bitrate * fractions.get(q, .1), optimize=False) for q in range(8)]
        result = compile_scenario(s)
        for load in result['loads']:
            node = nodes[load['node']]
            queue = node.ports[load['port']].queues[load['queue']]
            if queue.shaper == 'cbs':
                queue.optimize = True
        devices = sum(n.kind == 'device' for n in s.nodes)
        s.name = f'{PROFILES.index(self.profile)+1:02d} {self.profile.name} | {devices} devices, {len(s.nodes)-devices} switches, {len(s.flows)} flows'
        return s


def describe(s, result):
    nodes = {n.id: n for n in s.nodes}
    seen_links = set()
    link_for_port = {(ep.node, ep.port): link.id for link in s.links for ep in [link.a, link.b]}
    for load in result['loads']:
        seen_links.add(link_for_port[load['node'], load['port']])
    rates = {(ep.node, ep.port): link.bitrate for link in s.links for ep in [link.a, link.b]}
    port_load = defaultdict(float)
    for load in result['loads']:
        port_load[load['node'], load['port']] += load['bps']
    max_port_offered_ratio = max(value / rates[key] for key, value in port_load.items())
    hops = [len(path) for targets in result['paths'].values() for path in targets.values()]
    packets = 0
    for flow in s.flows:
        clock_rate = 1 + nodes[flow.source].clock_drift_ppm * 1e-6
        period = flow.burst_period if flow.sending_mode == 'burst' else flow.interval
        packets += math.ceil((s.experiment.duration - flow.start / clock_rate) / (period / clock_rate)) * (flow.burst_size if flow.sending_mode == 'burst' else 1)
    return dict(nodes=len(s.nodes), devices=sum(n.kind == 'device' for n in s.nodes),
                switches=sum(n.kind == 'switch' for n in s.nodes), links=len(s.links),
                active_links=len(seen_links), flows=len(s.flows),
                burst_flows=sum(f.sending_mode == 'burst' for f in s.flows),
                multicast_flows=sum(len(f.receivers)>1 for f in s.flows),
                max_hops=max(hops), expected_source_packets=packets,
                max_port_offered_ratio=round(max_port_offered_ratio,6),
                optimizer_variables=len(result['manifest']['variables']), warnings=len(result['diagnostics']))


def generate(seed_base=None):
    for profile in PROFILES:
        seed = profile.seed if seed_base is None else seed_base + PROFILES.index(profile)
        scenario = Builder(profile, seed).scenario()
        result = compile_scenario(scenario)
        yield profile, scenario, result, describe(scenario, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify committed templates without writing')
    parser.add_argument('--seed-base', type=int, help='Regenerate frozen design inputs from another deterministic seed base')
    args = parser.parse_args()
    mismatches = []
    for profile, scenario, result, info in generate(args.seed_base):
        path = ROOT / 'scenarios' / profile.filename
        text = scenario.model_dump_json(indent=2) + '\n'
        if args.check:
            if not path.exists() or path.read_text() != text:
                mismatches.append(profile.filename)
        else:
            path.write_text(text)
        print(profile.filename + ': ' + json.dumps(info, sort_keys=True))
    if mismatches:
        raise SystemExit('Templates differ from generator: ' + ', '.join(mismatches))


if __name__ == '__main__':
    main()
