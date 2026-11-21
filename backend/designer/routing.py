"""Enumerate loop-free, VLAN-admitted routes in increasing hop count."""
import heapq
from collections import deque
from itertools import islice
from pydantic import Field
from .schema import Model, Scenario


class RouteQuery(Model):
    scenario: Scenario
    flow_id: str
    receiver: str
    offset: int = Field(0, ge=0)
    limit: int = Field(25, ge=1, le=100)


def route_options(query: RouteQuery):
    s = query.scenario
    flows = [f for f in s.flows if f.id == query.flow_id]
    if len(flows) != 1:
        raise ValueError('Choose a unique flow identifier before selecting routes')
    flow = flows[0]
    nodes = {n.id: n for n in s.nodes}
    target = query.receiver
    if (flow.source not in nodes or target not in nodes or target not in flow.receivers
            or target == flow.source or nodes[flow.source].kind != 'device' or nodes[target].kind != 'device'):
        raise ValueError('Choose a sender and an existing end-device receiver')
    graph = {n: set() for n in nodes}
    for link in s.links:
        a, b = link.a, link.b
        if a.node not in nodes or b.node not in nodes or a.node == b.node:
            continue
        if flow.vlan in nodes[a.node].ports[a.port].vlans and flow.vlan in nodes[b.node].ports[b.port].vlans:
            graph[a.node].add(b.node)
            graph[b.node].add(a.node)

    # Same deterministic link order as compiler BFS, including equal-hop ties.
    ordered = {n: [] for n in nodes}
    for link in sorted(s.links, key=lambda link: link.id):
        for a, b in [(link.a.node, link.b.node), (link.b.node, link.a.node)]:
            if a in graph and b in graph[a] and b not in ordered[a]:
                ordered[a].append(b)

    def shortest(source, destination, excluded_nodes=frozenset(), excluded_edges=frozenset()):
        queue = deque([(source, [source])])
        seen = {source}
        while queue:
            node, path = queue.popleft()
            if node == destination:
                return path
            if node != source and nodes[node].kind == 'device':
                continue
            for other in ordered[node]:
                if other not in seen and other not in excluded_nodes and (node, other) not in excluded_edges:
                    seen.add(other)
                    queue.append((other, path + [other]))
        return None

    multicast = len(flow.receivers) > 1 or bool(flow.multicast_mac)
    default = shortest(flow.source, target) if multicast else shortest(target, flow.source)
    if default and not multicast:
        default.reverse()

    def paths():
        # Yen's algorithm avoids materializing every possible path at once.
        if not default:
            return
        accepted = [default]
        candidates = []
        seen = {tuple(default)}
        yield default
        while True:
            previous = accepted[-1]
            for i in range(len(previous) - 1):
                root = previous[:i + 1]
                if nodes[root[-1]].kind == 'device' and root[-1] != flow.source:
                    continue
                banned_edges = {(p[i], p[i + 1]) for p in accepted if len(p) > i + 1 and p[:i + 1] == root}
                spur = shortest(root[-1], target, set(root[:-1]), banned_edges)
                if spur:
                    candidate = tuple(root[:-1] + spur)
                    if candidate not in seen:
                        heapq.heappush(candidates, (len(candidate), candidate))
                        seen.add(candidate)
            if not candidates:
                return
            _, candidate = heapq.heappop(candidates)
            accepted.append(list(candidate))
            yield list(candidate)

    page = list(islice(paths(), query.offset, query.offset + query.limit + 1))
    saved = flow.routes.get(target)
    saved_valid = bool(saved and saved[0] == flow.source and saved[-1] == target
                       and len(saved) == len(set(saved)) and all(n in nodes for n in saved)
                       and all(nodes[n].kind == 'switch' for n in saved[1:-1])
                       and all(b in graph[a] for a, b in zip(saved, saved[1:])))
    return dict(default_path=default, paths=page[:query.limit], has_more=len(page) > query.limit,
                offset=query.offset, saved_path=saved if saved_valid else None,
                saved_unavailable=bool(saved and not saved_valid))
