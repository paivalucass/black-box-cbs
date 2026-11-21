# Native INET scenario generation

Compiler **3.3** uses the structure of `sample.ned` and `sample.ini`, with runnable parameter prefixes and one generated `[General]` section. It emits standard INET modules only. The Vue 3 frontend has four sidebar sections: Experiment, Build Simulation, Run Simulation, and Analysis. Build contains Topology (including node, queue and link settings), Traffic, and Review. Run contains a simulation selector, jobs and logs. Choose a saved simulation or the current editor draft before launching. Analysis has a separate completed-run selector and two views: Latency and Per link flow occupancy. Both use Vue/ECharts; see [analysis.md](analysis.md). Flat panels and square controls support both Dark and White appearance modes; the selection is saved locally.

## Scenario and network names

The saved simulation name is a human-readable label. The Experiment tab's **NED network name** controls the generated network type and filename, for example `ned/AutomotiveSample.ned` and `black_box_cbs.generated.AutomotiveSample`.

Networks extend `TsnNetworkBase`, with explicit `TsnDevice`/`TsnSwitch` port indices assigned automatically from connections. Standard 100 Mb/s, 1 Gb/s and 10 Gb/s links use `Eth100M`, `Eth1G` and `Eth10G`; other rates use `EthernetLink`. The user sets cable length in metres; NED sets the native Ethernet channel `length` parameter and inherits `delay = length / 2e8` seconds. Delay is displayed read-only in the GUI, never entered independently. Interfaces use `StreamingTransmitter` and `DestreamingReceiver`, and INET's default 96-bit IFG. There are no added switch-processing/PHY delay stages or IVN modules.

The generic IPv4 and MAC forwarding configurators are disabled. Devices bypass bridging (`hasBridging = false`) so Ethernet socket BIND requests reach the Ethernet command processor. Switches retain bridging and ingress/egress VLAN admission. IP, TCP and UDP are disabled on devices.

## Traffic tab

| GUI option | Scenario field | Meaning |
| --- | --- | --- |
| Sender | `source` | Single-interface end device |
| Receivers | `receivers` | One or more end devices; a multicast source generates once and the network replicates |
| Multicast MAC | `multicast_mac` | Optional explicit non-reserved group address; generated automatically for multiple receivers |
| Route to receiver | `routes[receiver]` | Dropdown of loop-free VLAN-valid paths, shortest by hop count first; automatic shortest path is the default |
| Payload | `payload` | Application bytes, excluding Ethernet/VLAN/FCS/PHY overhead |
| Sending mode | `sending_mode` | `periodic`, `burst`, or `once` |
| Sending period | `interval` | Local-clock seconds between periodic messages |
| Messages per burst | `burst_size` | Number generated at the same time |
| Burst period | `burst_period` | Local-clock pause between bursts |
| Start offset | `start` | Sender-local clock offset; queueing can delay wire transmission |
| Deadline | `deadline` | Simulation-time seconds after generation; only deadlines reached by simulation end enter the miss ratio |
| VLAN / PCP | `vlan`, `pcp` | PacketTagger settings; VLAN 1–4094 and PCP 0–7 |

Device and traffic-flow configuration rows start closed; click a row to open only the settings being edited. Receivers are chosen in a dropdown with checkboxes, without Ctrl/Shift selection.

Every flow uses `EthernetSourceApp`, `PacketTagger`, and `FlowMeasurementStarter`. Every receiver gets `EthernetSinkApp` with `FlowMeasurementRecorder`. Source packets are named `<flow-id>-<counter>`. Receiver recorders filter the flow's name prefix, preventing measurements from socket copies for other flows from entering that flow's results. Sockets bind the destination MAC, source MAC, and C-tag protocol; multicast sockets join the group through INET.

Burst generation uses INET's sequence expression, for example:

```ini
*.Lidar.app[0].source.productionInterval = seq("Lidar_Points") % 10 == 9 ? 10ms : 0s
```

Periodic and burst sources keep scheduling packets until OMNeT++ reaches the experiment simulation end. There is no per-flow stop condition or drain interval. Once mode remains an explicit single-message mode using `inf s`. Payload measurements use native flow region tags; no custom source/sink packet format or sequence-number module is emitted.

## Shared node clocks

Set drift in **Build Simulation → Topology**, once per node. All applications on a sender use the same `SettableClock` with `ConstantDriftOscillator`, a nominal 10 ns tick and `source.clockModule = "^.^.clock"`. Start and intervals use this clock. Positive ppm means a faster clock; periods in simulation time are approximately `local_period / (1 + ppm * 1e-6)`, subject to clock tick rounding.

Clocks do not enable gPTP and do not alter Ethernet bitrates. The sample's `Time_Sync` application is ordinary configured Ethernet traffic, not an implementation of IEEE 802.1AS.

## Routes, VLANs and physical cycles

Connected physical graphs may contain cycles. To add a link, choose two nodes, bitrate and cable length. The system creates one interface for each end and assigns contiguous indices in link order. Removing a link compacts indices while preserving the surviving connections’ queue and VLAN settings. There is no manual port count/index control. End devices support one connection; switches allocate up to 64 interfaces. End devices cannot be transit nodes. Duplicate connections between the same pair of nodes are rejected.

Automatic unicast routing uses destination-rooted shortest paths, so flows to the same MAC/VLAN agree on forwarding. Automatic multicast routing uses a source-rooted shortest-path tree. The route dropdown obtains paths from `POST /api/routes`, using a paged Yen loop-free path enumerator. It lists all VLAN-admitted simple routes, shortest by number of links first; **Load more routes** retrieves further choices if necessary. Selecting a path saves the complete node list. Switching back to automatic removes that override. Saved routes made unavailable by topology/VLAN edits are flagged, rather than silently replaced. Choices are physical paths; Review still checks their consistency with other unicast flows and multicast branches. Validation rejects loops, missing links/nodes, conflicting unicast forwarding, multicast paths with conflicting parents, and any route whose VLAN is not admitted at every traversed port.

The generated VLAN-aware static FDB includes only the selected output ports, with multiple entries for multicast fanout. Shared multicast links are counted once in offered-load diagnostics. Automatic multicast addresses avoid explicitly configured groups; address/VLAN pairs must be unique per flow. Broadcast traffic and arbitrary additional control traffic are outside this static-routed profile: adding them on a cyclic graph would require a separate loop-prevention design.

## Port queues and shaping

Configure 1–8 logical traffic classes and 1–8 physical FIFO queues independently:

```text
PCP → traffic class → physical queue
```

The compiler composes both mapping tables into `PcpClassifier.pcpToGateIndex` with `mode = "both"`. Classes sharing a queue share its capacity and reservation. Higher physical queue indices **always** have higher priority: 7 is highest and 0 lowest with eight queues (with fewer queues, the highest existing index is highest). The compiler always sets `reverseOrder = true`; this is not a user option. PCP/class mappings remain configurable.

| Port configuration | Native implementation |
| --- | --- |
| Strict priority only | `PriorityQueue`, `numQueues`, `scheduler.reverseOrder` |
| At least one CBS queue | `Ieee8021qTimeAwareShaper`, `numTrafficClasses`, `transmissionSelection.reverseOrder` |
| CBS queue | `transmissionSelectionAlgorithm[i].typename = "Ieee8021qCreditBasedShaper"` and `.idleSlope` |
| Non-CBS queue in a mixed port | Default pass-through selection stage |
| Every physical queue | Finite packet buffer capacity, drop-tail overflow |

**Queue buffer capacity (packets)** is the maximum number of packets waiting in that physical queue, independent of packet byte length. For capacity 100, at most 100 packets wait there; a newly arriving packet is dropped when the queue is full. This maps directly to INET `queue[i].packetCapacity`. `inet::queueing::PacketAtCollectionEndDropper` is the configured overflow policy: it selects the packet at the collection’s end after insertion exceeds capacity. With these unsorted FIFO queues, this drops the new arrival (drop-tail); scheduling and CBS still decide when retained packets can transmit.

**Simulation end** is the only generation horizon for periodic/burst traffic. Start offsets and intervals use the sender's local clock. Packets generated too late to arrive can remain queued or in transit at the end; these are reported as missing at the horizon, not confirmed drops. Packets whose deadlines have not yet matured are reported under **Deadlines not yet reached** and excluded from the deadline-miss denominator. No additional drain period is added.

Time-aware shaper transmission gates remain open with empty duration schedules; TAS is not enabled. CBS credit bounds and other gate policies retain INET defaults. No `ShapedQueue` wrapper is generated. An optimizer candidate still uses `node/port/queue` as its stable key, now mapped to the standard transmission-selection algorithm path.

## Automotive reference template

Import `scenarios/reference/sample-automotive.json` to reproduce the supplied topology: 20 nodes, 21 links, 18 source flows and 27 receiver paths. This reference is preserved separately from the five larger networks in [the current template catalogue](research-templates.md). It preserves connection-order port assignments, sample traffic modes/offsets/PCP/payloads, node drift, CBS reservations, and explicit paths derived from the supplied FDB. Legacy lower-index-first ports are migrated by reversing their physical queue configurations and class→queue mapping together. The effective class priorities are preserved while every native scheduler now serves higher queue indices first.

The reference INI has duplicated content, bare explanatory headings, and incomplete parameter prefixes; it is a behavioral reference, not copied literally. It does not specify VLAN IDs or deadlines: the reviewed template explicitly uses VLAN 10, deadline 10 ms and simulation end 1.1 s, with periodic/burst generation continuing to that end. These are editable scenario choices. Switch/PHY processing delays remain excluded.

## Migration and artifacts

Schema v1/v2/v3 remain accepted and normalize to schema v4. Old per-flow `stop` settings are accepted for import compatibility but omitted from saved/generated models and ignored for new runs. Frozen historical run extraction retains its original stop horizon; existing artifacts are not regenerated. Loading converts `destination` to `receivers`, supplies default network name `Scenario`, and moves a sender's legacy per-flow drift to its node when values agree. Conflicting legacy drifts require choosing one node drift before compilation. Legacy endpoint indices are read only to locate the existing per-connection configuration before automatic reallocation. A legacy delay without cable length is converted to the equivalent length (`delay * 2e8` metres), preserving its propagation behavior. Current models use cable length as the source of truth. Legacy lower-index-first queues migrate together with their mappings to preserve service order under the fixed higher-index-first rule. The frontend also normalizes local drafts.

Existing run directories and manifests are not regenerated. Measurement v1/v2 archives keep their legacy extraction logic. New runs record measurement v3 and per-receiver recorder paths. See [results-pipeline.md](results-pipeline.md) for definitions and limits.

Generated bundles contain NED, INI, canonical scenario JSON, resolved routes/FDB/load diagnostics, a versioned manifest and a Cmdenv launcher. Enter a working `opp_env` shell before `bash run.sh`; the launcher includes INET's NED exclusions. GUI execution uses the existing terminal-only job service.

## Validation

Compiler/schema, routing, migration, legacy archive, native telemetry, official scavetool export, NED syntax and Vue build checks run locally. They do not establish runtime behavior of the complete native model. A fresh Cmdenv launch is blocked in this sandbox because `opp_env` cannot open its workspace Git/flake context; complete the optional native tests on the working host before using measurements for research.

Per-link timing capture is enabled by default in Experiment and uses native observers without extra simulated delays. It can be disabled to reduce trace storage. Earlier runs lack the required packet-level link observations and need a new simulation for the occupancy chart.
