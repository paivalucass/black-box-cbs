"""Read official scavetool CSV-R; measure generated-packet cohorts."""
import bisect
import csv
import math
import statistics

def summary(values):
    v=sorted(values)
    if not v: return dict(count=0,mean=None,min=None,max=None,p50=None,p95=None,p99=None,stddev=None)
    def quantile(p):
        i=(len(v)-1)*p; low=int(i); high=min(low+1,len(v)-1)
        return v[low]+(v[high]-v[low])*(i-low)
    return dict(count=len(v),mean=statistics.mean(v),min=v[0],max=v[-1],p50=quantile(.5),p95=quantile(.95),p99=quantile(.99),stddev=statistics.pstdev(v))

def extract(path, scenario, manifest):
    vectors={}; scalars=[]
    with open(path,newline='') as stream:
        for r in csv.DictReader(stream):
            if r.get('type')=='vector':
                times=[float(v) for v in (r.get('vectime') or '').split()]
                values=[float(v) for v in (r.get('vecvalue') or '').split()]
                if len(times)!=len(values): raise ValueError('Malformed exported vector')
                vectors[(r['module'],r['name'])]=list(zip(times,values))
            elif r.get('type')=='scalar':
                try: val=float(r['value'])
                except (ValueError,KeyError): continue
                if math.isfinite(val): scalars.append(dict(module=r['module'],name=r['name'],value=val))
    results={}
    for f in scenario.flows:
        mods=manifest['flow_modules'][f.id]
        source_key=(mods['source'],'packetLengths:vector')
        # A zero-packet source can omit vectors; confirm via its scalar count.
        if source_key not in vectors and not any(r['module']==mods['source'] and r['name']=='packets:count' and r['value']==0 for r in scalars):
            raise ValueError(f'Missing required source observations for {f.id}')
        generated=sorted(t for t,_ in vectors.get(source_key,[]) if scenario.experiment.warmup<=t<f.stop)
        sink_key=(mods['sink'],'packetLifeTime:vector')
        if sink_key not in vectors and not any(r['module']==mods['sink'] and r['name']=='packets:count' and r['value']==0 for r in scalars):
            raise ValueError(f'Missing required sink observations for {f.id}')
        arrivals=vectors.get(sink_key,[])
        received={}; duplicates=0; unmatched=0; order=[]
        tolerance=min(f.interval/100,1e-9)
        for arrival,delay in arrivals:
            if not math.isfinite(delay): raise ValueError('Non-finite packet lifetime')
            creation=arrival-delay
            if creation < scenario.experiment.warmup-tolerance or creation>=f.stop: continue
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
        results[f.id]=dict(generated=len(generated),received=len(received),missing_at_horizon=missing,missing_at_horizon_ratio=missing/len(generated) if generated else None,confirmed_drops=None,duplicates=duplicates,out_of_order=sum(b<a for a,b in zip(order,order[1:])),deadline_misses=missed,deadline_denominator=len(matured),deadline_miss_ratio=missed/len(matured) if matured else None,latency_seconds=summary(delays),ipdv_seconds=summary(ipdv),mean_absolute_ipdv_seconds=statistics.mean(map(abs,ipdv)) if ipdv else None,ipdv_skipped_pairs=max(0,len(generated)-1-len(ipdv)),goodput_bps=len(received)*f.payload*8/(scenario.experiment.duration-scenario.experiment.warmup),latency_trace=[dict(time=received[j][0],delay=received[j][1]) for j in sorted(received)])
    return dict(measurement_version=1,flows=results,scalars=scalars,traces=[dict(module=m,name=n,samples=[list(x) for x in v]) for (m,n),v in vectors.items() if 'queue' in m],observation_end=scenario.experiment.duration,goodput_interval=[scenario.experiment.warmup,scenario.experiment.duration])
