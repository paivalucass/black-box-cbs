# Research network templates

The Experiment template dropdown contains five complex Ethernet/TSN scenarios. Load one, enter your simulation name, and save it as an independent model. Every value remains editable in Build Simulation. Reload the page after updating the repository to fetch the new catalogue; previously saved simulations and run snapshots are preserved.

## Catalogue

Device counts exclude switches. Flows count sources; multicast flows have multiple receiver paths.

| Template | Devices | Switches | Links | Flows | Burst flows | Multicast flows | Longest path |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Automotive zonal mesh | 48 | 12 | 69 | 198 | 72 | 23 | 7 hops |
| Industrial production network | 56 | 14 | 81 | 231 | 84 | 28 | 7 hops |
| Redundant backbone ring | 64 | 16 | 92 | 264 | 96 | 31 | 6 hops |
| Leaf–spine compute fabric | 96 | 16 | 144 | 396 | 144 | 46 | 4 hops |
| Cascaded CBS bottlenecks | 48 | 12 | 59 | 198 | 72 | 23 | 9 hops |

- **Automotive zonal mesh:** six access zones, four distribution switches and two core switches. VLAN-selected uplinks carry competing sensor bursts, safety commands and telemetry. Access links are 100 Mb/s or 1 Gb/s; core links are 10 Gb/s.
- **Industrial production:** seven cells, four distribution switches and a three-switch core. Control commands, sensor data, multicast heartbeats and maintenance traffic cross multiple aggregation levels. The role names are generic workload labels, not a model of a particular factory.
- **Backbone ring:** eight access sites and eight backbone switches connected by a ring with four opposite-site chords. All ring/chord links carry configured traffic. Redundancy is physical: these templates do not implement failure recovery or FRER.
- **Leaf–spine fabric:** twelve leaves connect to all four spines. Each VLAN uses a different spine, ensuring all 48 leaf–spine links carry traffic. Many-to-one bursts create contention at collector egress ports.
- **Cascaded bottlenecks:** six access cells feed a six-switch transit chain with two 100 Mb/s links between 1 Gb/s segments. Reduced CBS reservations and finite buffers deliberately create an overload experiment. Eight compiler reservation warnings are expected; measured losses and delays require running the simulation.

Static route validation confirms that every physical link carries at least one configured receiver path. Paths are chosen automatically by shortest hop count within admitted VLANs. Physical cycles use static forwarding for configured streams rather than flooding or a spanning-tree protocol.

## Traffic and queue settings

Each group has eight end devices with camera, lidar, radar, controller, actuator, compute, logger and gateway roles. Every device generates four independent streams: role-specific data, status, diagnostic bursts and a heartbeat. Each group also contributes a synchronized bulk burst to one shared compute collector.

Examples include 24-packet camera bursts every 4 ms, 20-packet lidar bursts every 5 ms, 80-byte commands every 0.5 ms, and background diagnostic/logging bursts. Normal collector bursts contain 33–67 packets every 10 ms, depending on network size. The cascaded case uses slower sensor cadences and 24-packet collector bursts every 40 ms. Collector burst sources share a 40 ms start offset; other streams use staggered phases. Periodic and burst traffic continues until simulation end.

All eight PCP values and four VLANs are exercised: VLAN 10 for control/heartbeats, 20 for status/services, 30 for sensor/bulk data, and 40 for diagnostics/logs. Payloads span 80–1400 bytes. Selected sensor, command and heartbeat flows are multicast. Node clock drift is fixed per node, sampled from −55 to +60 ppm, with both signs present in every template.

Each port has eight queues and eight traffic classes. PCP, class and queue indices map directly; queue 7 has the highest priority. Device queues use strict priority. Switch queues 4, 5 and 6 use CBS, with idleSlope set to 65%, 20% and 10% of that port's link rate respectively. Other queues use strict priority. Only CBS queues carrying traffic are exposed as optimizer variables, producing 84–132 variables per scenario.

The cascaded case reduces those reservations to 10%, 12% and 10% on 100 Mb/s switch ports. Queue capacities are finite packet buffers: normally 48–256 packets by class, reduced to 24–128 on those stressed ports. Cable lengths are stored in the model and the compiler derives propagation delay.

## Load and recording horizon

| Template | Duration | Warm-up | Seed | Estimated source packets | Busiest port nominal load |
| --- | ---: | ---: | ---: | ---: | ---: |
| Automotive | 160 ms | 20 ms | 2401 | 25,308 | 81.3% |
| Industrial | 200 ms | 20 ms | 2402 | 36,102 | 73.6% |
| Ring | 180 ms | 20 ms | 2403 | 36,242 | 69.1% |
| Leaf–spine | 120 ms | 12 ms | 2404 | 33,805 | 68.5% |
| Cascaded | 200 ms | 20 ms | 2405 | 16,926 | 117.0% |

These are configuration-derived estimates, not measured results. Source estimates include configured cadence and clock drift, before warm-up filtering; simulator time rounding can affect boundary counts. Nominal port load comes from the compiler's offered-load calculation and multicast routes. Four baselines pass its average queue-reservation checks without warnings. Average checks do not guarantee bounded latency or zero loss under synchronized bursts, strict-priority interference or finite buffers.

Short default horizons keep detailed per-link occupancy recording manageable. Link recording is enabled in all templates; extend the duration and warm-up in Experiment for longer evaluations. The roughly 17,000–36,000 source packets can produce substantially more records across hops and multicast branches. Packets still queued or in flight at the simulation end remain missing at the horizon; there is no traffic drain period.

Schema validation, routing, load checks, API compilation and NED syntax checks cover the catalogue. Native execution is a separate host acceptance check:

```bash
TSN_NATIVE_TESTS=1 PYTHONPATH=backend python3 -m pytest -q -k complex_research_native_execution
```

Run this in a working OMNeT++/INET `opp_env` environment. Native simulation could not be verified in the implementation sandbox because its Git/flake context prevents fresh `opp_env` launches.

## Regenerate reproducibly

From `black-box-cbs/`, using Python with the project's dependencies installed:

```bash
python3 scripts/generate_research_templates.py --check
python3 scripts/generate_research_templates.py
python3 scripts/generate_research_templates.py --seed-base 3000
```

`--check` verifies committed JSON without writing. The default generator uses seeds 2401–2405; `--seed-base 3000` uses 3000–3004 and overwrites the five generated templates. The generator validates/compiles each scenario and prints its traffic, routing and load summary. It does not run Cmdenv or alter saved models.

The design seed chooses fixed payloads, intervals, phases, cable lengths and clock drift, which are then stored in the JSON. Changing only the simulator seed in the GUI does not regenerate those frozen inputs. Use the generator with a different seed base to obtain a new deterministic workload realization, then save that model before comparing algorithms.

Small legacy scenarios remain in `tests/fixtures/legacy-scenarios/` for regression checks. The supplied 20-node automotive example is preserved in `scenarios/reference/sample-automotive.json` and can be imported manually; the original `sample.ini` and `sample.ned` are untouched. Neither the fixtures nor the reference appears in the template dropdown.
