"""Versioned scenario types. Values use seconds, bytes and bits/second."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Queue(Model):
    shaper: Literal['sp', 'cbs'] = 'sp'
    capacity: int = Field(100, ge=1, le=1000000)
    idle_slope: float = Field(20000000, gt=0)
    optimize: bool = False

class Port(Model):
    index: int = Field(ge=0, le=63)
    vlans: list[int] = Field(default_factory=lambda: [10])
    pcp_map: list[int] = Field(default_factory=lambda: [0,0,0,0,1,1,1,1], min_length=8, max_length=8)
    queues: list[Queue] = Field(default_factory=lambda: [Queue(), Queue(shaper='cbs')], min_length=1, max_length=8)

class Node(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    kind: Literal['device', 'switch'] = 'device'
    ports: list[Port] = Field(default_factory=lambda: [Port(index=0)])
    x: float = 100
    y: float = 100

class Endpoint(Model):
    node: str
    port: int = Field(ge=0)

class Link(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    a: Endpoint
    b: Endpoint
    bitrate: float = Field(100000000, gt=0, le=100000000000)
    delay: float = Field(0.000001, ge=0)

class Flow(Model):
    id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,40}$')
    source: str
    destination: str
    payload: int = Field(1000, ge=1, le=1472)
    interval: float = Field(0.001, gt=0)
    start: float = Field(0, ge=0)
    stop: float = Field(1, gt=0)
    vlan: int = Field(10, ge=1, le=4094)
    pcp: int = Field(4, ge=0, le=7)
    deadline: float = Field(0.01, gt=0)

class Experiment(Model):
    duration: float = Field(1.1, gt=0, le=3600)
    warmup: float = Field(0, ge=0)
    seed: int = Field(1, ge=0, le=2147483647)

class Scenario(Model):
    schema_version: Literal[1] = 1
    name: str = Field('CBS baseline', min_length=1, max_length=100)
    nodes: list[Node] = Field(min_length=2, max_length=256)
    links: list[Link] = Field(min_length=1, max_length=255)
    flows: list[Flow] = Field(min_length=1, max_length=512)
    experiment: Experiment = Field(default_factory=Experiment)
