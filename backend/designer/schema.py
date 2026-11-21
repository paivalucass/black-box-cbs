"""Versioned scenario types. Values use seconds, bytes and bits/second."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Queue(Model):
    shaper: Literal['sp', 'cbs'] = 'sp'
    capacity: int = Field(100, ge=1, le=1000000)
    idle_slope: float = Field(20000000, gt=0)
    optimize: bool = False

class Port(Model):
    index: int = Field(ge=0, le=63)
    higher_queue_index_first: bool = True
    vlans: list[int] = Field(default_factory=lambda: [10])
    pcp_map: list[int] = Field(default_factory=lambda: [0,0,0,0,1,1,1,1], min_length=8, max_length=8)
    class_to_queue: list[int] | None = Field(default=None, min_length=1, max_length=8)
    queues: list[Queue] = Field(default_factory=lambda: [Queue(), Queue(shaper='cbs')], min_length=1, max_length=8)

    @model_validator(mode='after')
    def legacy_class_mapping(self):
        if self.class_to_queue is None:
            self.class_to_queue = list(range(len(self.queues)))
        if not self.higher_queue_index_first:
            self.queues.reverse()
            self.class_to_queue = [len(self.queues)-1-q for q in self.class_to_queue]
        self.higher_queue_index_first = True
        return self

class Node(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    kind: Literal['device', 'switch'] = 'device'
    ports: list[Port] = Field(default_factory=lambda: [Port(index=0)])
    clock_drift_ppm: float | None = Field(default=None, ge=-100000, le=100000)
    x: float = 100
    y: float = 100

class Endpoint(Model):
    node: str
    port: int | None = Field(default=None, ge=0)

class Link(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    a: Endpoint
    b: Endpoint
    bitrate: float = Field(100000000, gt=0, le=100000000000)
    length_m: float | None = Field(default=None, ge=0)
    delay: float = Field(0.00000005, ge=0)  # Derived; accepted for legacy imports only.

    @model_validator(mode='after')
    def cable_delay(self):
        if self.length_m is None:
            self.length_m = self.delay * 200000000
        self.delay = self.length_m / 200000000
        return self

class Flow(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    source: str
    destination: str = ''  # Legacy single-receiver alias.
    receivers: list[str] = Field(default_factory=list)
    multicast_mac: str | None = None
    routes: dict[str, list[str]] = Field(default_factory=dict)
    payload: int = Field(1000, ge=1, le=1500)
    interval: float = Field(0.001, ge=1e-9)
    sending_mode: Literal['periodic', 'burst', 'once'] = 'periodic'
    burst_size: int = Field(10, ge=1, le=100000)
    burst_period: float = Field(0.01, ge=1e-9)
    clock_drift_ppm: float = Field(0, ge=-100000, le=100000)
    start: float = Field(0, ge=0)
    legacy_stop: float | None = Field(None, alias='stop', exclude=True, gt=0)  # Frozen old runs only.
    vlan: int = Field(10, ge=1, le=4094)
    pcp: int = Field(4, ge=0, le=7)
    deadline: float = Field(0.01, gt=0)

class Experiment(Model):
    record_link_occupancy: bool = True
    duration: float = Field(1.1, gt=0, le=3600)
    warmup: float = Field(0, ge=0)
    seed: int = Field(1, ge=0, le=2147483647)

class Scenario(Model):
    schema_version: Literal[1, 2, 3, 4] = 4
    ned_name: str = Field('Scenario', pattern=r'^[A-Za-z][A-Za-z0-9_]*$')
    name: str = Field('CBS baseline', min_length=1, max_length=100)
    nodes: list[Node] = Field(default_factory=list, max_length=256)
    links: list[Link] = Field(default_factory=list, max_length=255)
    flows: list[Flow] = Field(default_factory=list, max_length=512)
    experiment: Experiment = Field(default_factory=Experiment)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value=value.strip()
        if not value: raise ValueError('Enter a simulation name')
        return value

    @model_validator(mode='after')
    def migrate_models(self):
        self.schema_version=4
        for flow in self.flows:
            if not flow.receivers and flow.destination: flow.receivers=[flow.destination]
            flow.destination=''  # Canonical v2 uses receivers exclusively.
        for node in self.nodes:
            drifts={f.clock_drift_ppm for f in self.flows if f.source==node.id}
            if node.clock_drift_ppm is None and len(drifts)<=1:
                node.clock_drift_ppm=next(iter(drifts),0)
        assign_ports(self)
        return self


def assign_ports(scenario):
    """Assign contiguous interfaces by link order, preserving each link's settings.

    Old endpoint indices locate imported port settings; they never choose the new
    interface. New links omit indices and receive a default port configuration.
    """
    nodes = {n.id: n for n in scenario.nodes}
    previous = {n.id: {p.index: p for p in n.ports} for n in scenario.nodes}
    assigned = {n.id: [] for n in scenario.nodes}
    consumed = set()
    for link in scenario.links:
        link.cable_delay()
        for endpoint in (link.a, link.b):
            if endpoint.node not in nodes:
                continue  # Semantic validation reports the missing node.
            index = len(assigned[endpoint.node])
            if index >= 64:
                raise ValueError(f'{endpoint.node}: at most 64 Ethernet connections are supported')
            key = (endpoint.node, endpoint.port)
            old = previous[endpoint.node].get(endpoint.port) if key not in consumed else None
            port = old.model_copy(deep=True) if old else Port(index=index)
            port.index = index
            port.legacy_class_mapping()
            assigned[endpoint.node].append(port)
            if endpoint.port is not None:
                consumed.add(key)
            endpoint.port = index
    for node in scenario.nodes:
        node.ports = assigned[node.id]
