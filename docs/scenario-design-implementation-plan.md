# TSN scenario designer and simulation runner: implementation plan

Status: implemented with Vue 3 and Cmdenv. Compiler 3.2 now follows the supplied sample NED/INI using native INET applications, standard CBS, multicast/static routing, shared node clocks and native flow measurements. See [the current configuration contract](simulation-configuration.md) and [measurement definitions](results-pipeline.md). The original roadmap below includes later features; it does not imply support for every TSN profile.

## 1. Research purpose and initial deliverable

The methodology in `tcc_plan.pdf` starts with networks of increasing complexity with fixed topology and traffic. The later experiments vary CBS idleSlope values and compare BO, GA, CMA-ES, PSO, DE, and Random Search for quality, simulation efficiency, scalability, and reliability.

Build a local web application that lets Lucas design an Ethernet TSN scenario, inspect and export its NED/INI files, launch it without the OMNeT++ IDE, and inspect reproducible measurements. The first release must support the complete path from editing to a measured CBS simulation. Optimizers are a later consumer of this same simulation service.

The essential design decision is to save a **versioned scenario JSON document** as the source of truth. A shared compiler validates that document and generates the simulation files. Both the GUI and a CLI call the compiler and runner; an optimizer must not need to drive the browser.

First-release completion means creating a multi-switch scenario with multiple UDP flows, payload sizes, VLAN IDs, PCP values, explicit class mappings, finite priority queues and CBS settings; generating its files; launching it from the browser; and viewing latency, jitter, loss accounting and deadline results per flow.

## 2. Findings from this workspace

- The application repository is `black-box-cbs/`. Its `src/` contains only `package.ned`; `simulations/omnetpp.ini` contains only `[General]`.
- `simulations/run` expects an application executable and currently supplies only the application NED paths. The application Makefile does not yet configure INET linking.
- OMNeT++ 6.4.0 and INET 4.7.0 are present. Both release and debug INET shared libraries exist. The runner will use Cmdenv.
- INET already provides `TsnDevice`, `TsnSwitch`, `UdpSourceApp`, `UdpSinkApp`, VLAN policies, PCP classification, priority scheduling, and CBS. Reuse these models.
- A reference launch using `source omnetpp-6.4.0/setenv -f` and `opp_run -u Cmdenv` was rejected with `This OMNeT++ installation cannot be used outside an opp_env shell.` No simulation ran. Environment setup is therefore an explicit first milestone; presence of binaries is not evidence of a working runner.

Keep implementation, examples and documentation in `black-box-cbs/`. Do not modify OMNeT++ or INET to build the editor.

## 3. Simulation execution: Cmdenv only

Run every simulation through Cmdenv, OMNeT++'s terminal-only runtime. The backend launches the process, and the browser displays logs, progress and results. No simulation window or OMNeT++ IDE is opened.

Provide these actions in the browser:

| Action | Behavior |
| --- | --- |
| Run simulation | Start a managed Cmdenv process; show logs, progress and final results in the web app. |
| Cancel run | Stop the managed process and retain incomplete artifacts and logs. |
| Copy command | Provide the exact reproducible Cmdenv terminal command. |

Use `-u Cmdenv` explicitly in every generated launch command. There is no runtime selector or graphical simulation launch action.

The topology canvas and results plots live in the browser. Simulation execution requires no desktop display. Mark results final only after simulation completion and successful extraction.

## 4. Architecture and proposed code layout

Use a local Vue 3/TypeScript frontend with a graph editor such as Vue Flow, and a Python backend with Flask and Pydantic. Python keeps the scenario service accessible to future optimization and analysis code. Use SQLite for named simulation models and job metadata, with simulation artifacts on disk. Serve locally on loopback by default.

No cloud hosting, account system, distributed job queue, or optimizer library is needed for the first release. Select and lock dependency versions when implementation starts, against the installed Python and JavaScript runtimes.

```text
Browser: topology canvas, forms, file preview, run monitor, plots
                         |
                     HTTP / SSE
                         |
             Local API and persistent job queue
                         |
        Shared scenario validation and compilation library
                    /                \
          NED/INI bundle         Simulation worker
                                      |
                          OMNeT++ / INET subprocess
                                      |
                       scavetool + packet measurements
                                      |
                          Versioned metrics JSON / CSV

CLI and future optimizers call the same library and job service.
```

Proposed repository layout:

```text
black-box-cbs/
  web/                         Vue 3/TypeScript application
  backend/designer/
    api/                       Scenarios, exports, jobs, result endpoints
    schema/                    Scenario definitions and migrations
    validation/                Graph, VLAN, traffic, queue and CBS checks
    compiler/                  Resolved model, NED/INI rendering, source map
    runner/                    Environment profiles, process lifecycle, queue
    metrics/                   Extraction, cohort accounting, summaries
    cli.py                     Validate, generate, run, extract
  src/telemetry/               Application measurement modules if needed
  scenarios/                   Reviewed, versioned scenario JSON examples
  simulations/reference/       Hand-authored minimal reference simulations
  tests/                       Compiler, runner, metrics and integration tests
  docs/
  var/                         Ignored database, generated bundles and runs
```

Do not create all these directories as empty scaffolding. Introduce them as working functionality is added. Preserve `black_box_cbs` as the NED package prefix. Build custom C++ modules only when the telemetry or an explicitly selected feature needs them.

## 5. GUI workflow

Use a tabbed scenario workspace with a black background and readable 17px controls and labels. Keep the Experiment, Topology, Nodes & queues, Links, Traffic, Features, Generated files, Runs and Results buttons as tabs. Only the active section appears, and configuration state persists when switching tabs.

1. **Create:** start empty or from the automotive zonal, industrial production, backbone ring, leaf–spine or cascaded-bottleneck research templates. Save a name and description. See [the catalogue](research-templates.md) for their workloads and deterministic generator.
2. **Wire:** add TSN end devices and switches; connect explicit numbered ports; edit link rate and propagation delay. Dragging a node affects layout only.
3. **Define traffic:** add named flows between end devices; configure payload, timing, VLAN, PCP and deadlines. Highlight each flow's resolved path.
4. **Configure ports:** edit ingress policies and egress traffic classes, capacities, schedulers and shapers. Show how each PCP maps to a queue on each traversed port.
5. **Validate:** place errors on the relevant object and field; distinguish unsupported configurations from intentional overload experiments.
6. **Generate:** preview NED/INI with source links back to GUI fields; download the complete bundle or copy the launch command.
7. **Run:** configure duration, warm-up, seed and measurement profile, then launch Cmdenv. Monitor the job, cancel it, and preserve its logs.
8. **Inspect:** view per-flow results and queue/port diagnostics; compare runs; export CSV and the full experiment bundle.

Include table editing for flows and port policies, duplication of devices/flows, undo/redo for topology edits, and save/reopen. These matter more initially than elaborate graphics. Save editable simulation models by their user-entered names, independently of templates. Save updates the opened model; Save as copy creates another named model. Preserve old revisions during migration. A run freezes its own immutable snapshot, so later model edits cannot change an ongoing experiment.

## 6. Scenario data contract

Use stable IDs distinct from display names and NED identifiers. Store physical values with explicit units and normalize them for validation. Keep a separate `layout` block so moving canvas objects does not invalidate a simulation cache.

| Object | Required fields and meaning |
| --- | --- |
| Scenario | Schema version, ID, revision, name, description, feature profile, topology-generation seed if applicable. |
| Node | Stable ID, valid generated NED name, kind (`TsnDevice` or `TsnSwitch`), ports, enabled node features. |
| Port | Stable ID, persistent Ethernet index, link reference, ingress policy, egress queue/class policy, VLAN behavior. |
| Link | ID, two explicit endpoint ports, full-duplex Ethernet rate, propagation delay. Both interface and channel rates resolve consistently. |
| VLAN policy | Tagged endpoint or access/trunk behavior, native/untagged policy, permitted VLANs, tag preservation or removal. |
| Flow | ID, source, destination, UDP port allocation, application payload bytes, period or approved distribution, start offset (generation continues until simulation end), VLAN, PCP, optional deadline. |
| Egress classes | 1–8 classes, complete PCP-to-class mapping, priority order, per-class FIFO capacity and drop behavior, shaper selection. |
| CBS policy | Per-node/per-port/per-class idleSlope, optional credit parameters, eligible-for-optimization flag, lower/upper bounds. |
| Experiment | Warm-up, simulation end, seed set, repetitions, recording profile, completion horizon and measurement definition version. |

First support fixed-size, periodic unicast UDP flows, including phase offsets and background traffic. Then add seeded variable intervals/payloads and burst profiles. Avoid arbitrary free-text INI expressions in the normal editor; typed distribution choices are easier to validate and reproduce.

Keep payload size distinct from Ethernet frame size and channel occupancy. Include protocol headers, VLAN tags, FCS, PHY overhead, IFG, and minimum-frame behavior where applicable when estimating load. Make fragmentation an explicit profile choice; reject oversize packets in the initial nonfragmenting profile. Do not reuse an untagged showcase's byte-overhead constant for all flows.

Give each initial flow a distinct destination socket on its receiving host, enabling reliable application-level attribution. Resolve addresses, source filters and app indices deterministically. If traffic specifications are randomized, save the realized flows as well as the generator seed; regenerating traffic inside every evaluation would undermine fixed scenarios.

## 7. Correct TSN, priority and VLAN modeling

### Egress scheduling and CBS

On an enabled TSN egress interface, INET's queue structure is:

```text
PCP classification -> per-class FIFO -> class selection gate (CBS or bypass)
                   -> time gate -> strict-priority scheduler -> Ethernet MAC
```

Use `hasEgressTrafficShaping = true` and `Ieee8021qTimeAwareShaper` as the supported queue container. The container name does not mean TAS must be active: leave time gates open for a CBS-only experiment. For a CBS class, set `transmissionSelectionAlgorithm[i].typename` to `Ieee8021qCreditBasedShaper`; for strict-priority-only classes, use the supported bypass configuration. Queue contents are FIFO within each traffic class, and the scheduler prioritizes among eligible classes.

The installed scheduler defaults to `reverseOrder = true`: larger class indices are served first. PCP is not automatically equal to the queue index. Compile the editor's eight-value PCP-to-class table into the installed classifier's mapping matrix and explicitly validate it for the chosen class count.

CBS idleSlope is attached to an **egress port and traffic class**, not directly to an individual flow. Multiple flows sharing a class share the shaper. Show that aggregation and its estimated offered load. Allow shaping on end-device egress ports as well as switch egress ports, with deliberate defaults so unintended source shaping does not obscure the experiment.

Keep `sendSlope = idleSlope - portBitrate` derived by default. Expose credit limits as an advanced profile, and record their effective values. Warn that model defaults such as unbounded credit are modeling choices; decide and freeze the credit policy in each research scenario rather than implying that all defaults are hardware-specific bounds.

### Ingress behavior

Separate receive-side classification, VLAN acceptance, and per-stream filtering/policing from transmit queues. INET's `hasIngressTrafficFiltering` enables Qci filtering/policing; it does not create a matching bank of ingress CBS queues.

The first release provides ingress VLAN admission and stream classification, plus egress priority queues and CBS. Add supported Qci policers as a subsequent feature profile. If actual buffered ingress priority queues are required, implement a separate application-owned INET-compatible module and validate its backpressure and forwarding behavior before enabling that option. Keep it visible as planned/unsupported until then; never silently translate an ingress queue request into an egress queue setting.

### VLAN behavior

Implement VLANs end to end. A VLAN number on a flow must produce a tag, permitted ingress/egress membership, compatible forwarding, and correct endpoint decoding or access-port stripping. Tagging alone does not establish isolation.

Use stream encoder mappings for `stream`, `vlan` and `pcp`; use INET VLAN policies and explicit forwarding where needed. A tagged receiving device must have the IEEE 802.1Q receive path enabled, for example through `hasIncomingStreams`. Do not globally strip VLAN tags using the CBS showcase's convenience setting when evaluating a VLAN-aware network.

Regular VLAN membership uses IDs 1–4094; expose untagged and priority-tagged VID 0 as separate modes, not ordinary VLAN memberships. Preserve PCP across forwarding and tag transitions according to an explicit policy. Check actual tags and class selection in a small packet capture before considering VLAN support complete.

## 8. NED and INI generation

Implement a compiler with four stages: schema validation, semantic validation, resolution into an explicit intermediate model, and deterministic rendering. The resolved model fixes app indices, port indices, addresses, paths, forwarding tables, class mappings and exact parameter locations before text generation.

Generate:

| Artifact | Purpose |
| --- | --- |
| `scenario.json` | Canonical saved scenario revision. |
| `resolved.json` | Explicit port/app/path assignments and effective policies. |
| `ned/package.ned` and network NED | Package hierarchy, device/switch modules, configurators and link connections. Canvas layout remains presentation metadata in the scenario JSON. |
| `omnetpp.ini` | Apps, streams, VLAN policies, rates, queues, CBS, routing/forwarding, seed and recording configuration. |
| `candidate.ini` | Only candidate CBS overrides, when supplied; base scenario remains unchanged. |
| `manifest.json` | Hashes, versions, dependency paths, flow/module map, optimization-variable map, measurement policy and commands. |
| Launch scripts/command description | Reproduce the Cmdenv run using the configured environment. |

Use an export-specific package under `black_box_cbs.generated`, with `ned/` as the corresponding NED source root. Use explicit `ethg[index]` connections and explicit `numEthInterfaces` declarations. Stable port IDs keep their assigned indices across edits; no connection-order-dependent `ethg++` assignments. Validate the produced package tree in the actual NED loader.

NED contains topology and module types; INI contains experiment parameters. Preserve canvas coordinates in the scenario JSON for reopening the web editor. Include MAC setup and deterministic forwarding, not just devices and links. The implemented Ethernet compiler resolves VLAN-aware paths and emits static forwarding tables, including validated multicast trees. Physical rings and meshes are supported with loop-free forwarding for the configured streams; this does not enable arbitrary broadcast/control traffic on cyclic graphs.

For illustration, these installed parameter locations are relevant; this fragment is not a complete generated scenario:

```ini
*.talker.hasOutgoingStreams = true
*.listener.hasIncomingStreams = true
*.talker.bridging.streamCoder.encoder.mapping = [{stream: "control", vlan: 10, pcp: 4}]

*.sw0.hasEgressTrafficShaping = true
*.sw0.eth[1].macLayer.queue.numTrafficClasses = 2
*.sw0.eth[1].macLayer.queue.queue[1].packetCapacity = 100
*.sw0.eth[1].macLayer.queue.transmissionSelectionAlgorithm[1].typename = "Ieee8021qCreditBasedShaper"
*.sw0.eth[1].macLayer.queue.transmissionSelectionAlgorithm[1].idleSlope = 20Mbps
```

Generate all required app identification, PCP mapping, VLAN membership, link settings and measurement configuration around this. Mark the generated preview read-only initially. Downloaded manual edits are valid external experiments, but are not automatically round-tripped into the structured editor or treated as the same scenario hash.

INI uses first-matching assignment precedence. Generate specific overrides before broad defaults, and verify candidate composition explicitly. A candidate wrapper can define a named configuration extending the generated base configuration. Never append candidate lines and assume they override an earlier matching wildcard.

## 9. Validation rules

Return structured diagnostics with severity, object ID, field and remedy.

- Reject duplicate IDs/NED names, invalid names, invalid units, occupied ports, incompatible rates and invalid interface indices.
- Check each flow's source/destination, unique receiving socket, connectivity, VLAN-permitted path, and forwarding path. Explicitly configure deterministic neighbor resolution or startup behavior and document it.
- Check VLAN/PCP ranges and endpoint tag support. Require valid mappings for all eight PCP values and all configured queues.
- Require positive payload, interval, finite queue capacities for loss experiments, valid timing, and warm-up earlier than simulation end.
- For enabled CBS, require `0 < idleSlope < portBitrate`; disable CBS explicitly instead of using zero idleSlope. Validate optimization bounds similarly.
- Give each scenario a per-port CBS reservation budget, at most link capacity by default. Reject budget violations in the normal profile. A separate overload/stress profile can deliberately relax admission assumptions and must label the resulting experiment.
- Estimate load after overhead and aggregate it along resolved paths and classes. Traffic exceeding its reservation or link capacity is an experimental warning, not automatically an invalid scenario. CBS does not guarantee a deadline merely because average load fits.
- Reject unavailable TSN features and unvalidated feature combinations rather than silently omitting them.
- Require a measurement horizon long enough for every measured packet's configured deadline to mature. Otherwise mark those observations censored and exclude them from finalized deadline comparisons.
- Ask OMNeT++ to load/initialize the generated network in a short validation run to catch missing types, bad parameter paths and routing problems. Text rendering alone is not validation.

## 10. Runner and artifact lifecycle

First implement an environment profile that finds the workspace, enters the existing `opp_env` environment, loads OMNeT++/INET paths and NED exclusions, and checks matching build modes. Do not fake the environment variables that guard this installation or automatically rebuild bundled dependencies.

Prefer `opp_run` loading the existing INET shared library for scenarios using only INET models. When custom telemetry is added, build an application shared library linked against INET and load both. Update the application build/launcher integration accordingly; the current scaffold cannot yet supply this.

The following is the intended shape of a command **inside the verified environment**, not a command validated in the present shell:

```bash
opp_run -u Cmdenv \
    -l /home/lucas/omnetpp/inet-4.7.0/src/INET \
    -n /home/lucas/omnetpp/inet-4.7.0/src:/path/to/bundle/ned \
    -f /path/to/bundle/omnetpp.ini -c Baseline -r 0 \
    --result-dir=/path/to/run/results
```

The environment adapter additionally supplies configured NED exclusions and application libraries as needed. Explicitly configure Cmdenv in the generated INI as well as the launch command so manual launches use the same runtime. Store the actual invocation and effective environment profile in each run manifest.

Jobs follow `queued -> validating -> running -> extracting -> completed`, with separate `failed`, `cancelled` and `interrupted` terminal states. Persist transitions and reconcile interrupted jobs after backend restart. Start with a single worker and serial execution; later parallelism requires isolated directories and explicit CPU/memory limits.

For every job:

1. Freeze the scenario revision, candidate, seed and recording policy in a new run directory.
2. Launch a backend-owned subprocess with structured arguments; never execute a shell command supplied by a form. An environment wrapper is fixed application code with correctly quoted paths.
3. Stream stdout/stderr and available simulation-time progress through SSE. Distinguish simulated time, wall time, CPU time and event count; completion time predictions are optional.
4. Support cancellation of the full process group, with graceful termination followed by a bounded forced stop. Retain partial artifacts as incomplete.
5. Check exit status, simulation termination reason and required output files before extraction. An existing `.sca` file alone is insufficient evidence of success.
6. Extract results with OMNeT++'s result tools; retain `.sca`, `.vec`, `.vci`, logs, manifest and normalized metrics. Do not hand-parse vector files as though they were ordinary CSV.

The browser can download logs, metrics and the exact bundle. Runs use isolated output paths and never overwrite a previous evaluation. Keep generated binaries, run outputs and the local database out of Git.

## 11. Measurements suitable for later optimization

Define metrics before building plots. Distinguish application payload goodput from Ethernet utilization, and observed latency from a theoretical worst-case bound.

Use one source/sink pair per flow and record packet identity, flow ID, generation time, reception time and payload length. Prefer INET creation-time and identity tags plus source/sink signals if they preserve all required information. Add small application-owned source/measurement modules where necessary, but do not introduce a per-flow traffic stop: periodic/burst generation continues until simulation end. Measurement tags must not silently increase simulated payload size.

INET already exposes sink `packetLifeTime`, `packetJitter` and related statistics, queue statistics and CBS credits. Verify their semantics against the chosen application boundary. Built-in scalars alone do not provide the required generated-packet cohort and missing/deadline accounting.

Define the measurement cohort by **generation time**, `warmup <= t_gen < simulation_end`. Do not add a separate source stop/drain interval. Report packets still in transit at the horizon as missing and exclude unmatured deadlines from deadline-miss comparisons. Ignore earlier generated packets for cohort metrics even if they arrive after warm-up. Record source attempts separately if a traffic source can be throttled by backpressure.

| Measurement | Definition and presentation |
| --- | --- |
| End-to-end latency | `t_receive_at_application_sink - t_generate_at_application_source`, in simulation time, for unique delivered cohort packets. Mean, min, max, p50, p95, p99 and sample count. Includes source and network queueing. |
| Delay variation | Standard deviation of delivered packet latency, labeled separately from jitter. |
| Jitter/IPDV | Signed difference in delay between consecutive source-sequence packets when both arrive. Report signed distribution plus mean absolute IPDV. Skip and count gaps/reordering cases under a documented policy; do not bridge gaps silently. |
| Delivery/loss accounting | Generated cohort count, unique received count, confirmed drops when observable, duplicates, out-of-order receptions and packets missing at the observation horizon. Do not label every missing packet as a confirmed network drop. |
| Deadline misses | With per-flow deadline `D`, a packet misses when no unique reception occurs by `t_gen + D`. Count both late and undelivered packets whose deadlines matured. Divide by all cohort packets with matured deadlines, not only received packets. |
| Goodput | Delivered unique application payload bits over the documented measurement interval. Preserve a received-bit time series as well. |
| Queue diagnostics | Per egress class occupancy, maximum occupancy, residence time and drops, where available; selectable traces for bottlenecks. |
| CBS diagnostics | Credit evolution, gate availability, configured idleSlope and achieved class throughput; selected traces in debug mode. |
| Port utilization | Defined busy time or wire occupancy divided by observation time, with the accounting convention recorded. |
| Evaluation cost | Wall/CPU time, event count, termination status, extraction time and cache status. Needed for later algorithm comparisons. |

Report metrics with units, sample counts and unavailable values. An empty sink must show zero deliveries and unavailable latency, never zero latency. A failed or incomplete run is not a feasible solution; packets with deadlines beyond a completed observation horizon are reported separately. Where the observation horizon cannot establish true physical loss, expose `missing_at_horizon_ratio` and the horizon alongside confirmed-drop metrics.

Provide two recording profiles. **Inspection** retains selected packet, queue and credit traces. **Evaluation** records the samples or sufficient summaries needed for the same metric definitions while disabling unrelated high-volume vectors. Validate that summaries agree on a small case before using evaluation mode. Compute percentiles from actual samples or an explicitly documented approximation, not from a mean scalar.

The results view contains a per-flow table; latency distribution/CDF and selected time series; jitter distribution; deadline and delivery counts; and selectable port/class queue and credit plots. Keep a drill-down from flow to path and bottleneck port. Compare repeated seeds with per-seed results and uncertainty; do not pool all packets across seeds and call that independent-run uncertainty.

## 12. Scope of TSN features

| Feature | Planned availability |
| --- | --- |
| TSN end devices/switches, Ethernet links, periodic UDP, VLAN/PCP | First working release. |
| Per-port egress class mapping, finite FIFOs, strict priority and CBS | First working release. CBS is the primary research shaper. |
| Arbitrary buffered ingress priority queues | Separate custom module milestone if required; not supplied by the basic TSN switch toggle. |
| Qci ingress filtering/policing | Next validated profile; expose rate/burst/filter fields, with measured drop reasons. |
| TAS and CBS+TAS | Later profile with cycle, offset, gate control list and clock assumptions. |
| ATS | Later profile with the installed INET composition and its own parameters; not represented as a CBS checkbox. |
| gPTP and clock drift | Later profile with grandmaster, port roles, clock model and synchronization traffic. Ideal simulation time is sufficient for the initial CBS-only baseline. |
| Frame preemption | Later profile after compatibility tests; it changes the interface/queue composition and cannot be assumed compatible with every CBS configuration. |
| FRER and cut-through | Later profiles with path, duplicate accounting and interaction tests. |
| Ring/mesh topology | After explicit loop-free forwarding/path validation; no unrestricted flooding loops. |

Represent capabilities and supported combinations in a backend registry. The GUI enables only implemented and tested combinations and explains their prerequisites. This keeps the schema extensible without claiming that all TSN standards or combinations already work.

## 13. Reproducibility and future optimizer interface

Freeze topology, traffic realizations, routing, buffers, features, measurement definitions and seed schedules for each research scenario. Treat canvas layout and display names as presentation metadata. Record semantic scenario hash, generated-file hashes, compiler version, OMNeT++/INET versions and library identities.

Expose the same operation to the GUI, CLI and future algorithms:

```text
evaluate(scenario_revision, candidate_idle_slopes, simulation_seed, recording_profile)
    -> job_id -> {status, feasibility_inputs, per_flow_metrics, cost, artifacts}
```

Publish optimization variables in a deterministic order, keyed by stable `(node_id, port_id, class_id)` tuples with bounds and target INI paths. The optimization dimension is the number of selected CBS port/class settings, not simply the number of flows or all queues. Apply candidate changes through typed overlays and reject changes to fixed scenario fields.

Keep physical infeasibility, invalid candidates, simulator failure and measurement incompleteness as distinct outcomes. Define objective weights, constraints and penalty policies before algorithm comparisons; do not choose an undocumented latency/loss weighted score in the GUI implementation. Initially return raw metrics and declared deadline/reservation checks.

Use separate topology-generation, traffic, simulation and later optimizer seeds. Provide a fixed common simulation-seed schedule for candidate comparisons and independent validation seeds for finalists. Record RNG mapping so adding a telemetry module does not silently alter stochastic traffic.

Cache only on the complete semantic evaluation identity, including candidate, seed, dependency versions and metric policy. Label cache hits and retain uncached timing. Later studies must distinguish logical evaluations, actual simulator calls and time, so caching cannot give an algorithm an unreported budget advantage.

## 14. Scenario families for the methodology

Provide these reviewed examples before a large random-network generator:

| Family | Purpose and fixed properties |
| --- | --- |
| Single-switch baseline | Two talkers, one switch and one listener; CBS control traffic plus competing best effort; explicit VLAN/PCP and a known bottleneck. |
| Multi-hop line | Increase switches, for example 1, 2, 4 and 8, with fixed flow definitions and controlled offered load. |
| Converging traffic | Multiple talkers converge on a shared egress port; expose class aggregation, burst competition and queue drops. |
| Branched network | Tree topology with several paths and shared bottlenecks; useful for automotive-style sensor/controller traffic. |
| Class/dimension scaling | Hold topology and traffic fixed while increasing selected CBS queues and optimization dimension. |
| Load sweep | Preserve topology and relative traffic composition while using documented interval/rate scaling to obtain low, medium, near-capacity and overload cases. |

Change one factor at a time initially. Save actual counts of switches, flows, classes, selected variables, hops, and per-port load; switch count alone is not network complexity. Do not claim that increased hop count must always increase every observed statistic under different traffic conditions.

For generated families, use deterministic graph templates and saved seeds, including physical cycles with validated static forwarding. The research template generator freezes topology, cable lengths, clock drift, traffic phases, payloads and burst settings into editable scenario JSON. Freeze the realized network and flows before optimization. A full factorial campaign and optimizer selection belong to later methodology phases.

## 15. Implementation sequence and acceptance criteria

| Milestone | Deliverable | Acceptance criterion |
| --- | --- | --- |
| 0. Environment and reference | Verified `opp_env` launch profile, INET library/NED discovery, tiny hand-authored CBS/VLAN scenario. | Reference completes in Cmdenv without a desktop display or simulation window; record exact commands and effective build mode. |
| 1. Shared scenario compiler | Typed schema, validation, stable port/app mapping, JSON example, CLI generation, complete NED/INI bundle. | A generated single-switch scenario loads and exchanges tagged traffic in INET; files are deterministic and invalid VLAN/class mappings are rejected. |
| 2. Measurement foundation | Packet attribution, continuous generation and horizon censoring, extraction and versioned metrics contract. | Independent tiny cases establish delay boundary, jitter, loss/missing distinction, deadlines and zero-delivery behavior. |
| 3. Web editor and preview | Topology canvas, node/link inspector, traffic table, ingress/egress forms, source-linked diagnostics and export. | Save/reopen and import/export the single-switch and line scenarios without semantic changes; the GUI calls milestone 1's compiler. |
| 4. Browser-managed execution | Job API, worker, SSE logs, cancellation, isolated runs and Cmdenv launch action. | Launch, complete and inspect a real run from the browser without opening a simulation window; cancel another; recover state after restart; copy a reproducible terminal command. |
| 5. Results and research scenarios | Flow table, distributions, queue/CBS plots, comparisons, reviewed line/tree/bottleneck families. | Multi-hop flows traverse the intended VLAN-permitted paths; results map to their correct flow/queue and repeated-seed runs preserve fixed scenarios. |
| 6. Feature extensions | Qci, TAS, ATS, clocks and other requested features, each behind capability validation. | Each profile and supported interaction has a runnable example and an explicit behavior check before its UI option is enabled. |
| Later: optimization | Candidate API consumers, objectives, budgets, repeated optimizer trials. | Algorithms use the established evaluator without changing scenario semantics or measurement definitions. |

Milestones 0–5 form the initial usable designer/evaluator. Start with a CLI-generated real simulation to establish correctness, then expose it in the browser. Do not implement the six optimizers during this work.

## 16. Verification plan

Add focused checks for behavior that can invalidate research results:

- **Compiler:** identifier escaping, unit conversion, stable port assignments, matrix mapping, VLAN path validation and INI override precedence. Identical semantic input produces identical simulation configuration.
- **Tagged baseline:** capture/observe emitted VLAN and PCP, verify ingress acceptance and actual egress queue assignment, and reject unauthorized VLAN traffic.
- **Priority:** controlled contention shows the configured service order among eligible queues. Distinguish FIFO ordering within a class from priority across classes.
- **CBS:** a backlogged class exhibits credit gating and its configured long-run reservation behavior within a documented finite-window/overhead tolerance; it remains work-conserving with the other eligible classes as modeled. Do not infer correctness from one visual plot alone.
- **Telemetry:** hand-check a tiny packet trace with known generation, arrival, duplicate, gap and deadline events; verify warm-up cohort selection, end-of-run censoring and loss denominators.
- **Runner:** successful run, bad NED/INI, invalid candidate, cancellation, backend restart and separate run directories. Failed runs cannot become completed cached results.
- **End to end:** create/edit/save/generate/run/inspect one bottleneck scenario through the browser, then repeat it with the copied command and compare the metric outputs.

When C++ instrumentation is introduced, regenerate application makefiles with INET linkage, build release and debug as appropriate, and run the affected command-line scenarios. Keep runtime-dependent tests separate from pure schema/compiler checks so missing `opp_env` support produces a clear prerequisite failure rather than a misleading model failure.

## 17. Local evidence and external references

The installed INET 4.7.0 source is authoritative for exact parameter paths in this workspace:

- `../inet-4.7.0/src/inet/node/tsn/TsnDevice.ned` and `TsnSwitch.ned`.
- `../inet-4.7.0/src/inet/linklayer/ieee8021q/Ieee8021qTimeAwareShaper.ned`, `Ieee8021qCreditBasedGate.ned`, and `PcpTrafficClassClassifier.ned`.
- `../inet-4.7.0/showcases/tsn/trafficshaping/creditbasedshaper/omnetpp.ini`.
- `../inet-4.7.0/examples/ethernet/vlan/omnetpp.ini` and the stream replication examples for VLAN-aware forwarding.
- `../inet-4.7.0/src/inet/protocolelement/redundancy/StreamEncoder.ned` and `StreamDecoder.ned`.
- `../inet-4.7.0/src/inet/queueing/base/PassivePacketSinkBase.ned` and `PacketSourceBase.ned`.
- `../inet-4.7.0/src/inet/queueing/source/ActivePacketSource.ned` and `../inet-4.7.0/src/inet/applications/udpapp/UdpApp.ned`.
- `../omnetpp-6.4.0/src/envir/evmain.cc` for this installation's `opp_env` guard.

The paths above are workspace-relative from the application root; from this `docs/` directory use one additional `..`.

Official background: [INET CBS showcase](https://inet.omnetpp.org/docs/showcases/tsn/trafficshaping/creditbasedshaper/doc/index.html), [INET 4.7 CBS API](https://inet.omnetpp.org/docs/4.7/api/types/inet.linklayer.ieee8021q.Ieee8021qCreditBasedShaper.html), and [OMNeT++ simulation execution](https://doc.omnetpp.org/manual/ch-run-sim). Online showcase descriptions may refer to a different INET release; verify generated configurations against the installed sources and runtime.
