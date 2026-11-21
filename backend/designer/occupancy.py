"""Per-port packet observations + native transmitter signals, never aggregate estimates."""
import json
import math
from pathlib import Path
from .results import read_results


def iter_json_array(path):
    """Read INET's potentially large packet-event arrays one record at a time."""
    decoder=json.JSONDecoder()
    with Path(path).open() as stream:
        buffer='';eof=False;started=False;need_separator=False
        while True:
            buffer=buffer.lstrip()
            if not buffer and not eof:
                chunk=stream.read(65536);buffer+=chunk;eof=not chunk
                continue
            if not started:
                if not buffer.startswith('['):raise ValueError('Packet event file is not a JSON array')
                buffer=buffer[1:];started=True;continue
            if buffer.startswith(']'):
                if (buffer[1:]+stream.read()).strip():raise ValueError('Unexpected data after packet event array')
                return
            if need_separator:
                if not buffer.startswith(','):raise ValueError('Malformed packet event array separator')
                buffer=buffer[1:];need_separator=False;continue
            try:
                value,end=decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:raise ValueError('Incomplete or invalid native packet event file')
                chunk=stream.read(65536);buffer+=chunk;eof=not chunk
                continue
            if not isinstance(value,dict):raise ValueError('Invalid packet event observation')
            yield value
            buffer=buffer[end:];need_separator=True


def number(value):
    value=float(value)
    if not math.isfinite(value):raise ValueError('Non-finite packet timing')
    return value


def time_tolerance(a,b):
    # The official export uses 15 significant digits; native event JSON retains fs.
    return max(1e-12,abs(a)*1e-14,abs(b)*1e-14)


def transmitter_intervals(samples,horizon):
    intervals=[];start=None;previous=-1
    for time,state in samples:
        time=number(time);state=number(state)
        if time<previous or state not in (0,1):raise ValueError('Invalid transmitter state history')
        previous=time
        if state==1 and start is None:start=time
        elif state==0 and start is not None:
            intervals.append((start,min(time,horizon),False));start=None
    if start is not None and start<horizon:intervals.append((start,horizon,True))
    return intervals


def process_link_occupancy(bundle,scenario,manifest,exported):
    profile=manifest.get('link_occupancy')
    metadata=dict(version=1,available=False,directions=[],observation_end=scenario.experiment.duration,
        coverage='Packets reaching each sending PHY, including transmissions in progress at simulation end. Packets still solely in queues at the horizon are not captured.',
        semantics='Queueing occurs at the sending port. Transmission is serializer occupancy. Propagation is the last-bit tail after transmission; propagation of earlier bits overlaps transmission.')
    if not profile or not profile.get('enabled'):
        metadata['reason']='Per-link packet timing was not recorded for this run. Enable recording and launch a new simulation.'
        return metadata
    vectors,scalars=read_results(exported)
    horizon=scenario.experiment.duration
    for config in profile['directions']:
        info={k:v for k,v in config.items() if k not in ['observer','transmitter','queue','event_file']}
        info.update(available=False,packet_count=0,segment_count=0,first_time=None)
        destination=bundle/'results'/f'occupancy-{config["source"]}-{config["port"]}.jsonl'
        temporary=destination.with_suffix('.jsonl.tmp')
        try:
            raw=bundle/'results'/config['event_file']
            if not raw.exists():raise ValueError('Native per-port packet observations are missing. Launch a new run with the updated compiler.')
            states=transmitter_intervals(vectors.get((config['transmitter'],'transmitting:vector'),[]),horizon)
            propagation=next((r['value'] for r in scalars if r['module']==config['transmitter'] and r['name']=='propagationTime'),None)
            durations={}
            for (module,name),samples in vectors.items():
                if module==config['transmitter'] and name.endswith(':flowTransmissionTime:vector'):
                    durations[name[:-len(':flowTransmissionTime:vector')]]=samples
            position=0;duration_positions={};observed_flows=set(config['flow_ids'])
            with temporary.open('w') as output:
                for observation in iter_json_array(raw):
                    if observation.get('module')!=config['observer']:raise ValueError('Unexpected native observer module')
                    start=number(observation['simulationTime'])
                    if start>=horizon:continue
                    if position>=len(states):raise ValueError('Packet observation has no native transmission start')
                    tx_start,tx_end,censored=states[position];position+=1
                    if abs(tx_start-start)>time_tolerance(tx_start,start):raise ValueError('Packet observation and transmitter start disagree')
                    packet=str(observation['packetName']);flow,separator,sequence=packet.rpartition('-')
                    if not separator or not sequence.isdigit():flow='Unclassified traffic'
                    observed_flows.add(flow)
                    if not censored:
                        index=duration_positions.get(flow,0);samples=durations.get(flow,[])
                        if index>=len(samples):raise ValueError('Native per-flow transmission durations are missing')
                        end,duration=samples[index];duration_positions[flow]=index+1
                        if abs(end-tx_end)>time_tolerance(end,tx_end) or abs(duration-(tx_end-tx_start))>time_tolerance(tx_start,tx_end):
                            raise ValueError('Native transmission duration disagrees with transmitter states')
                    segments=[];seen_events=set()
                    for region in observation.get('packetEvents',[]):
                        for event in region.get('events',[]):
                            if event.get('type')!='QUEUED' or not event.get('module','').startswith(config['queue']+'.'):continue
                            key=(packet,event['module'],event['eventNumber'])
                            if key in seen_events:continue
                            seen_events.add(key)
                            end=number(event['simulationTime']);duration=number(event['duration'])
                            if duration<0 or end>tx_start+1e-12 or end-duration< -1e-12:raise ValueError('Invalid native queue residence interval')
                            if duration>0:segments.append(dict(kind='queueing',start=max(0,end-duration),end=end,censored=False,module=event['module']))
                    segments.append(dict(kind='transmission',start=tx_start,end=tx_end,censored=censored,module=config['transmitter']))
                    if not censored:
                        if propagation is None or propagation<0:raise ValueError('Native propagation-time scalar is missing')
                        if propagation>0 and tx_end<horizon:
                            segments.append(dict(kind='propagation',start=tx_end,end=min(horizon,tx_end+propagation),censored=tx_end+propagation>horizon,module=config['transmitter']))
                    record=dict(flow_id=flow,packet=packet,packet_tree_id=observation.get('packetTreeId'),segments=segments)
                    output.write(json.dumps(record,allow_nan=False)+'\n')
                    info['packet_count']+=1;info['segment_count']+=len(segments)
                    earliest=min(s['start'] for s in segments)
                    info['first_time']=earliest if info['first_time'] is None else min(info['first_time'],earliest)
            if position!=len(states):raise ValueError('Native transmission is missing its packet observation')
            temporary.replace(destination)
            info.update(available=True,data_file=destination.name,flow_ids=sorted(observed_flows))
        except (ValueError,KeyError,TypeError,OSError) as exc:
            temporary.unlink(missing_ok=True)
            info['reason']=str(exc)
        metadata['directions'].append(info)
    metadata['available']=any(d['available'] for d in metadata['directions'])
    metadata['partial']=any(not d['available'] for d in metadata['directions'])
    if not metadata['available']:metadata['reason']='Native per-link timing is unavailable: '+next((d['reason'] for d in metadata['directions'] if d.get('reason')),'no port observations')
    return metadata


def occupancy_window(bundle,metadata,key,start,end,limit=5000):
    start=number(start);end=number(end)
    if start<0 or end<=start or end>metadata['observation_end']+1e-12:raise ValueError('Choose a time window within the simulation, with end after start')
    if not 1<=limit<=10000:raise ValueError('Segment limit must be between 1 and 10000')
    direction=next((d for d in metadata['directions'] if d['key']==key),None)
    if direction is None:raise ValueError('Choose a recorded link direction')
    if not direction['available']:raise ValueError(direction.get('reason','Link recording is unavailable'))
    segments=[];matching=0
    with (bundle/'results'/direction['data_file']).open() as stream:
        for line in stream:
            packet=json.loads(line)
            for segment in packet['segments']:
                if segment['end']>start and segment['start']<end:
                    matching+=1
                    if len(segments)<limit:segments.append(dict(segment,flow_id=packet['flow_id'],packet=packet['packet'],packet_tree_id=packet['packet_tree_id']))
    return dict(link=key,start=start,end=end,segments=segments,total_matching=matching,truncated=matching>limit,limit=limit)
