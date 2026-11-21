# Analysis: latency and per-link packet timelines

Analysis selects a completed immutable run, independently of the editable simulation and run-log inspector. Its two sidebar tabs use Vue components and **Apache ECharts 6.0.0**, with theme-aware SVG rendering and SVG download buttons.

## Latency

The table shows received sample count and end-to-end minimum, maximum and mean in **milliseconds**, separately for every Ethernet flow and multicast receiver. It uses the run's native measured latency summary; it does not estimate delay from link settings.

Select a flow/receiver for the bar histogram. Equal-width bins count its actual `latency_trace` samples after converting seconds to milliseconds. The vertical axis is **Distribution (%)**, fixed at 0–100; the horizontal axis is **End-to-end latency (ms)**. Bin percentages sum to 100. This is a probability histogram, not a CDF. Constant-latency samples produce one nonzero-width bin. No received samples means unavailable latency, not zero.

Delivery/deadline statistics remain in an expandable table. Run comparison remains available. The metrics CSV includes min/max/mean in seconds, with units in its column names. The latency cohort uses source generation after warm-up, before simulation end.

## Per link flow occupancy

Choose a link **direction**, a simulation-time window in milliseconds, and either flow rows (matching `image.png`) or individual packet rows. A full-duplex cable has two independent directions. Flow colour stays consistent across link choices.

- Solid fill: the transmitter's serialization interval.
- Diagonal hatching: actual waiting in that sender port's physical queue.
- Dotted pattern: propagation of the last bit after serialization.

Use packet rows to distinguish overlapping waits of several packets of the same flow. The horizontal zoom slider and mouse wheel inspect the loaded window. Vertical zoom scrolls large row lists. Hover identifies the packet, phase, precise start/end/duration and whether the phase was clipped by simulation end. Each chart can be saved as SVG.

Queue waiting happens at the sending node; it does not occupy the physical serializer. Propagation of earlier bits overlaps transmission: the propagation bar intentionally shows the **last-bit tail**, so its end is the last-bit arrival at the far side. IFG and other stages are not relabelled as queueing; they remain gaps.

A response contains at most 5,000 plotted intervals. If more overlap the requested window, a notice reports the true matching count and asks for a narrower window. There is no averaging/downsampling of packet intervals. Every interval overlapping the window is eligible, including a wait that began before the window.

## Recording and reconstruction

New compiler **3.3** runs enable **Record per-link packet timelines** in Experiment by default. Disable it for campaigns that only need latency and should avoid the extra event-trace storage. No application C++ library or changes to INET are needed.

The compiler:

1. Enables native `packetEvent` region tags at each `FlowMeasurementStarter`.
2. Inserts a native `FlowMeasurementRecorder` in every Ethernet PHY's optional `cutthroughSink` slot, immediately before `StreamingTransmitter`.
3. Configures that observer to write `${resultdir}/link-events-<node>-<port>.json`. Its unmatched `flowName = "__link_observer__"` cannot equal a configured flow ID, so recording does not end the real flow's measurements. It records packet events only; endpoint latency recorders remain unchanged.
4. Retains the native transmitter `transmitting:vector`, per-flow `flowTransmissionTime:vector`, and `propagationTime` scalar in the official JSON export.
5. Records all exact module names, link directions, port indices, flow membership and event filenames in the manifest.

`occupancy.py` streams the event files, identifies the packet from its source-assigned name, and matches its observer timestamp to a native transmitter state transition. Completed transmissions are checked against the native per-flow duration. A native `QUEUED` event records **dequeue time** plus residence duration; its plotted start is `dequeue_time - duration`. Repeated events across payload regions are deduplicated per observation. Historical hops are filtered out: only that sending port's queues are shown.

Propagation uses the transmitter's recorded channel-delay scalar, not a guessed cable delay or an average end-to-end component. An unfinished transmitter state is clipped to the observation horizon and marked censored; it is not shown as a completed packet. No propagation interval is invented for a packet still transmitting.

Normalized per-direction records are written to `results/occupancy-<node>-<port>.jsonl`. Metadata and coverage appear in `metrics.json.link_occupancy`. The browser requests bounded windows with:

```text
GET /api/jobs/<id>/occupancy?link=<direction-key>&start=<seconds>&end=<seconds>&limit=5000
```

All timing data in artifacts/API uses seconds; Vue converts to milliseconds. Invalid/missing observations mark that direction unavailable, with a reason, instead of filling the chart with estimates. Other latency results remain usable.

### Coverage

The observer captures packets that reach the sending PHY, including transmissions still in progress at the horizon and packets later dropped farther along the route. It does not depend on arrival at the final receiver, and shared multicast hops are recorded once per physical transmission. Packets remaining solely inside queues at simulation end have not reached this observation point and have no timeline record. The chart displays this coverage explicitly.

The timeline covers the **full simulation**, including warm-up. Latency statistics use the separate configured measurement cohort. Earlier run archives without the observers cannot reconstruct packet-level queue/link intervals from aggregates: launch a new run for the timeline. Their latency tables/histograms remain available when packet samples were recorded.

## ECharts asset and offline use

The browser first loads `/vendor/echarts.min.js`, then falls back to the pinned ECharts 6.0.0 jsDelivr distribution. A loading error is explicit if neither is reachable. For offline research workstations, install the asset while online and rebuild:

```bash
cd web
npm run charts:install
npm run build
```

This copies the official distribution, preserving its license headers, into `web/public/vendor/echarts.min.js`; the build includes it in `dist/vendor/`. It requires no CDN access afterward. The asset is generated and ignored by Git. Dependency download was blocked by DNS restrictions in the implementation sandbox, so ECharts runtime rendering could not be exercised here. The Vue build and chart-data/option tests are independent of that download.

The custom-series renderer follows [Apache ECharts' custom-series API](https://echarts.apache.org/handbook/en/how-to/custom-series/), including explicit coordinate encoding, weak-filter data zoom and native decal patterns.

## Verification limits

Pure tests cover histograms, units, phase styles, packet/flow row grouping, native interval reconstruction, zero/constant samples, region-event duplication, window intersection, horizon censoring, missing data, streamed large JSON records, official transmitter-signal export and the read-only occupancy API. Native NED syntax and the Vue production build are checked.

A fresh native run was attempted, but `opp_env` could not open the sandbox workspace Git context, so full simulator validation of the added native observer remains a host-side check. Optional `TSN_NATIVE_TESTS=1` smoke tests now also require recorded link timing. Browser/ECharts rendering is likewise not claimed to have been verified in this environment.
