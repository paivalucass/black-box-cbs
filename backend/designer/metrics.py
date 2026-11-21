"""Compute flow metrics from official result exports; never simulate packets here."""
import bisect
import math
import statistics
from collections import defaultdict, deque

from .results import read_results

def summary(values):
    v=sorted(values)
    if not v: return dict(count=0,mean=None,min=None,max=None,p50=None,p95=None,p99=None,stddev=None)
    def quantile(p):
        i=(len(v)-1)*p; low=int(i); high=min(low+1,len(v)-1)
        return v[low]+(v[high]-v[low])*(i-low)
    return dict(count=len(v),mean=statistics.mean(v),min=v[0],max=v[-1],p50=quantile(.5),p95=quantile(.95),p99=quantile(.99),stddev=statistics.pstdev(v))

def generation_end(scenario, flow, manifest):
    """New runs use the experiment horizon; frozen archives keep their old cutoff."""
    if manifest.get('generation_horizon') == 'simulation_end':
        return scenario.experiment.duration
    return min(flow.legacy_stop if flow.legacy_stop is not None else scenario.experiment.duration,
               scenario.experiment.duration)


def extract(path, scenario, manifest):
    vectors, scalars = read_results(path)
    if manifest.get('measurement_version',1)>=3:
        return extract_native(vectors,scalars,scenario,manifest)
    results={}
    for f in scenario.flows:
        end=generation_end(scenario,f,manifest)
        mods=manifest['flow_modules'][f.id]
        source_key=(mods['source'],'packetLengths:vector')
        # A zero-packet source can omit vectors; confirm via its scalar count.
        if source_key not in vectors and not any(r['module']==mods['source'] and r['name']=='packets:count' and r['value']==0 for r in scalars):
            raise ValueError(f'Missing required source observations for {f.id}')
        source_observations=vectors.get(source_key,[])
        generated=sorted(t for t,_ in source_observations if scenario.experiment.warmup<=t<end)
        sequence_to_index=None
        if mods.get('source_sequence'):
            source_sequence=vectors.get((mods['source'],mods['source_sequence']),[])
            if len(source_sequence)!=len(source_observations): raise ValueError(f'Missing source sequence observations for {f.id}')
            sequence_to_index={}
            for (t,_),(seq_time,seq) in zip(source_observations,source_sequence):
                if abs(t-seq_time)>1e-12 or seq!=int(seq): raise ValueError(f'Invalid source sequence observations for {f.id}')
                if scenario.experiment.warmup<=t<end:
                    if int(seq) in sequence_to_index: raise ValueError('Source sequence numbers must be unique')
                    sequence_to_index[int(seq)]=len(sequence_to_index)
            sink_sequence=vectors.get((mods['sink'],mods['sink_sequence']),[])
        sink_key=(mods['sink'],'packetLifeTime:vector')
        if sink_key not in vectors and not any(r['module']==mods['sink'] and r['name']=='packets:count' and r['value']==0 for r in scalars):
            raise ValueError(f'Missing required sink observations for {f.id}')
        arrivals=vectors.get(sink_key,[])
        received={}; duplicates=0; unmatched=0; order=[]
        tolerance=min(f.interval/100,1e-9)
        if sequence_to_index is not None and len(sink_sequence)!=len(arrivals): raise ValueError(f'Missing sink sequence observations for {f.id}')
        for observation_index,(arrival,delay) in enumerate(arrivals):
            if not math.isfinite(delay): raise ValueError('Non-finite packet lifetime')
            creation=arrival-delay
            if creation < scenario.experiment.warmup-tolerance or creation>=end: continue
            if sequence_to_index is not None:
                seq_time,seq=sink_sequence[observation_index]
                if abs(seq_time-arrival)>1e-12 or seq!=int(seq): raise ValueError(f'Invalid sink sequence observations for {f.id}')
                j=sequence_to_index.get(int(seq))
                if j is None: unmatched+=1; continue
            else:
                i=bisect.bisect_left(generated,creation)
                possible=[j for j in [i-1,i] if 0<=j<len(generated)]
                if not possible: unmatched+=1; continue
                j=min(possible,key=lambda j:abs(generated[j]-creation))
            if abs(generated[j]-creation)>tolerance: unmatched+=1; continue
            if j in received: duplicates+=1; continue
            received[j]=(arrival,delay); order.append(j)
        if unmatched: raise ValueError(f'{f.id}: {unmatched} receptions could not be attributed to actual generations')
        delays=[received[j][1] for j in sorted(received)]
        ipdv=[received[j][1]-received[j-1][1] for j in sorted(received) if j-1 in received]
        matured=[j for j,t in enumerate(generated) if t+f.deadline<=scenario.experiment.duration]
        missed=sum(j not in received or received[j][0]>generated[j]+f.deadline+tolerance for j in matured)
        missing=len(generated)-len(received)
        results[f.id]=dict(generated=len(generated),received=len(received),missing_at_horizon=missing,missing_at_horizon_ratio=missing/len(generated) if generated else None,confirmed_drops=None,duplicates=duplicates,out_of_order=sum(b<a for a,b in zip(order,order[1:])),deadline_misses=missed,deadline_denominator=len(matured),deadline_unmatured=len(generated)-len(matured),cohort_end=end,deadline_miss_ratio=missed/len(matured) if matured else None,latency_seconds=summary(delays),ipdv_seconds=summary(ipdv),mean_absolute_ipdv_seconds=statistics.mean(map(abs,ipdv)) if ipdv else None,ipdv_skipped_pairs=max(0,len(generated)-1-len(ipdv)),goodput_bps=len(received)*f.payload*8/(scenario.experiment.duration-scenario.experiment.warmup),latency_trace=[dict(time=received[j][0],delay=received[j][1]) for j in sorted(received)])
    return dict(measurement_version=manifest.get('measurement_version',1),flows=results,scalars=scalars,traces=[dict(module=m,name=n,samples=[list(x) for x in v]) for (m,n),v in vectors.items() if 'queue' in m],observation_end=scenario.experiment.duration,goodput_interval=[scenario.experiment.warmup,scenario.experiment.duration])


def native_vector(vectors, module, statistic, flow):
    """INET prefixes the statistic with its flow label and ends it with ':vector'. Never merge flows."""
    return vectors.get((module,f'{flow}:{statistic}:vector'))


def extract_native(vectors, scalars, scenario, manifest):
    results={}
    for f in scenario.flows:
        end=generation_end(scenario,f,manifest)
        modules=manifest['flow_modules'][f.id]
        source=vectors.get((modules['source'],'packetLengths:vector'))
        if source is None:
            if not any(s['module']==modules['source'] and s['name']=='packets:count' and s['value']==0 for s in scalars):
                raise ValueError(f'Missing required source observations for {f.id}')
            source=[]
        generated=sorted(t for t,_ in source if scenario.experiment.warmup<=t<end)
        for receiver, sink in modules['receivers'].items():
            arrivals=native_vector(vectors,sink['recorder'],'meanBitElapsedTimePerPacket',f.id)
            if arrivals is None:
                # Demux vectors are created lazily, only on the first reception.
                if not any(s['module']==sink['sink'] and s['name']=='packets:count' for s in scalars):
                    raise ValueError(f'Missing sink instrumentation for {f.id} at {receiver}')
                arrivals=[]
            received={};order=[]
            groups=defaultdict(deque)
            for j,t in enumerate(generated):groups[t].append(j)
            generation_times=sorted(groups)
            tolerance=min((f.burst_period if f.sending_mode=='burst' else f.interval)/100,1e-9)
            for arrival,delay in arrivals:
                if not math.isfinite(delay) or delay<0:raise ValueError('Invalid native elapsed time')
                creation=arrival-delay
                if creation<scenario.experiment.warmup-tolerance or creation>=end:continue
                lo=bisect.bisect_left(generation_times,creation-tolerance)
                hi=bisect.bisect_right(generation_times,creation+tolerance)
                possible=[t for t in generation_times[lo:hi] if groups[t]]
                matched=min(possible,key=lambda t:abs(t-creation)) if possible else None
                index=groups[matched].popleft() if matched is not None else None
                if index is None:
                    raise ValueError(f'{f.id}@{receiver}: reception cannot be attributed to an unused generation; native measurements do not identify duplicates')
                received[index]=(arrival,delay);order.append(index)
            delays=[received[j][1] for j in order]
            jitter=[b-a for a,b in zip(delays,delays[1:])]
            matured=[j for j,t in enumerate(generated) if t+f.deadline<=scenario.experiment.duration]
            missed=sum(j not in received or received[j][0]>generated[j]+f.deadline+tolerance for j in matured)
            missing=len(generated)-len(received)
            breakdown={}
            for dimension,stat in [('queueing','meanTotalBitQueueingTimePerPacket'),('processing','meanTotalBitProcessingTimePerPacket'),('transmission','meanTotalPacketTransmissionTime'),('propagation','meanTotalBitPropagationTimePerPacket'),('delaying','meanTotalBitDelayingTimePerPacket')]:
                observations=native_vector(vectors,sink['recorder'],stat,f.id)
                if observations is not None:
                    if len(observations)!=len(arrivals):raise ValueError('Native delay dimensions have inconsistent sample counts')
                    breakdown[dimension]=summary([observations[i][1] for i,(t,d) in enumerate(arrivals) if scenario.experiment.warmup-tolerance<=t-d<end])
            key=f.id if len(modules['receivers'])==1 else f'{f.id}@{receiver}'
            results[key]=dict(flow_id=f.id,receiver=receiver,generated=len(generated),received=len(received),missing_at_horizon=missing,missing_at_horizon_ratio=missing/len(generated) if generated else None,confirmed_drops=None,duplicates=None,out_of_order=None,deadline_misses=missed,deadline_denominator=len(matured),deadline_unmatured=len(generated)-len(matured),cohort_end=end,deadline_miss_ratio=missed/len(matured) if matured else None,latency_seconds=summary(delays),ipdv_seconds=summary(jitter),mean_absolute_ipdv_seconds=statistics.mean(map(abs,jitter)) if jitter else None,ipdv_skipped_pairs=None,goodput_bps=len(received)*f.payload*8/(scenario.experiment.duration-scenario.experiment.warmup),latency_trace=[dict(time=received[j][0],delay=received[j][1]) for j in order],delay_breakdown_seconds=breakdown)
    return dict(measurement_version=3,generation_horizon=manifest.get('generation_horizon','legacy_flow_stop'),latency_definition='INET mean bit elapsed time per packet, source measurement starter to receiver recorder',jitter_definition='Difference of successive received packet delays (INET packetJitter); warm-up cohort applied',identity_limit='Native timing measurements do not identify duplicate or reordered packets; these fields are unavailable',flows=results,scalars=scalars,traces=[dict(module=m,name=n,samples=[list(x) for x in v]) for (m,n),v in vectors.items() if '.macLayer.queue.' in m or n=='busy:vector'],observation_end=scenario.experiment.duration,goodput_interval=[scenario.experiment.warmup,scenario.experiment.duration])
