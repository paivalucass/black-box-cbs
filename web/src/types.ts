export interface Queue { shaper:'sp'|'cbs'; capacity:number; idle_slope:number; optimize:boolean }
export interface Port { index:number; vlans:number[]; pcp_map:number[]; queues:Queue[] }
export interface Device { id:string; kind:'device'|'switch'; ports:Port[]; x:number; y:number }
export interface Endpoint { node:string; port:number }
export interface Link { id:string; a:Endpoint; b:Endpoint; bitrate:number; delay:number }
export interface Flow { id:string; source:string; destination:string; payload:number; interval:number; start:number; stop:number; vlan:number; pcp:number; deadline:number }
export interface Scenario { schema_version:1; name:string; nodes:Device[]; links:Link[]; flows:Flow[]; experiment:{duration:number; warmup:number; seed:number} }
export const queue = ():Queue => ({shaper:'sp',capacity:100,idle_slope:20000000,optimize:false})
export const port = (index:number):Port => ({index,vlans:[10],pcp_map:[0,0,0,0,1,1,1,1],queues:[queue(),{...queue(),shaper:'cbs'}]})
