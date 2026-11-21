export interface Queue { shaper:'sp'|'cbs'; capacity:number; idle_slope:number; optimize:boolean }
export interface Port { index:number; higher_queue_index_first:boolean; vlans:number[]; pcp_map:number[]; class_to_queue:number[]; queues:Queue[] }
export interface Device { id:string; kind:'device'|'switch'; ports:Port[]; clock_drift_ppm:number|null; x:number; y:number }
export interface Endpoint { node:string; port?:number }
export interface Link { id:string; a:Endpoint; b:Endpoint; bitrate:number; length_m:number; delay:number }
export interface Flow { id:string; source:string; destination:string; receivers:string[]; multicast_mac:string|null; routes:Record<string,string[]>; payload:number; interval:number; start:number; vlan:number; pcp:number; deadline:number; sending_mode:'periodic'|'burst'|'once'; burst_size:number; burst_period:number; clock_drift_ppm:number }
export interface Scenario { schema_version:1|2|3|4; ned_name:string; name:string; nodes:Device[]; links:Link[]; flows:Flow[]; experiment:{record_link_occupancy?:boolean; duration:number; warmup:number; seed:number} }
export const queue = ():Queue => ({shaper:'sp',capacity:100,idle_slope:20000000,optimize:false})
export const port = (index:number):Port => ({index,higher_queue_index_first:true,vlans:[10],pcp_map:[0,0,0,0,1,1,1,1],class_to_queue:[0,1],queues:[queue(),{...queue(),shaper:'cbs'}]})
export function normalizeScenario(input:Scenario):Scenario {
 const s=JSON.parse(JSON.stringify(input)) as Scenario
 s.schema_version=4;s.ned_name??='Scenario';s.experiment.record_link_occupancy??=true
 for(const n of s.nodes){
  if(n.clock_drift_ppm==null){const drifts=[...new Set(s.flows.filter(f=>f.source===n.id).map(f=>f.clock_drift_ppm??0))];n.clock_drift_ppm=drifts.length>1?null:drifts[0]??0}
  for(const p of n.ports)normalizePort(p)
 }
 for(const f of s.flows){delete (f as Flow & {stop?:unknown}).stop;f.sending_mode??='periodic';f.burst_size??=10;f.burst_period??=.01;f.clock_drift_ppm??=0;f.receivers??=f.destination?[f.destination]:[];f.multicast_mac??=null;f.routes??={};f.destination=''}
 normalizeTopology(s)
 return s
}

function normalizePort(p:Port){
 p.class_to_queue??=p.queues.map((_,i)=>i)
 if(p.higher_queue_index_first===false){
  p.queues.reverse()
  p.class_to_queue=p.class_to_queue.map(q=>p.queues.length-1-q)
 }
 p.higher_queue_index_first=true
}

export function normalizeTopology(s:Scenario){
 const old=new Map(s.nodes.map(n=>[n.id,new Map(n.ports.map(p=>[p.index,p]))]))
 const assigned=new Map(s.nodes.map(n=>[n.id,[] as Port[]]))
 const consumed=new Set<string>()
 for(const link of s.links){
  link.length_m??=(link.delay??.00000005)*200000000
  link.delay=link.length_m/200000000
  for(const endpoint of [link.a,link.b]){
   const ports=assigned.get(endpoint.node)
   if(!ports)continue
   const key=JSON.stringify([endpoint.node,endpoint.port])
   const previous=endpoint.port==null||consumed.has(key)?undefined:old.get(endpoint.node)?.get(endpoint.port)
   const next=previous?{...previous,index:ports.length}:port(ports.length)
   normalizePort(next)
   ports.push(next)
   if(endpoint.port!=null)consumed.add(key)
   endpoint.port=next.index
  }
 }
 for(const n of s.nodes)n.ports=assigned.get(n.id)!
}
