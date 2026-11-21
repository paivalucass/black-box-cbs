import type { ChartOption } from './echarts'
export interface HistogramBin { lower:number; upper:number; count:number; percentage:number }
export interface Segment {kind:'queueing'|'transmission'|'propagation';start:number;end:number;censored:boolean;flow_id:string;packet:string;packet_tree_id?:number;module?:string}
export function histogram(delays:number[],requestedBins=20):HistogramBin[]{
 const values=delays.filter(v=>Number.isFinite(v)&&v>=0).map(v=>v*1000)
 if(!values.length)return []
 let min=Infinity,max=-Infinity
 for(const v of values){min=Math.min(min,v);max=Math.max(max,v)}
 const count=min===max?1:Math.max(1,Math.min(100,Math.floor(requestedBins)||20))
 const width=min===max?Math.max(min*.02,.000001):(max-min)/count
 const lower=min===max?Math.max(0,min-width/2):min
 const bins=Array.from({length:count},(_,i)=>({lower:lower+i*width,upper:lower+(i+1)*width,count:0,percentage:0}))
 for(const value of values)bins[Math.min(count-1,Math.max(0,Math.floor((value-lower)/width)))].count++
 for(const bin of bins)bin.percentage=100*bin.count/values.length
 return bins
}
export function colours(theme:'dark'|'light'){
 return theme==='dark'?{text:'#eee',muted:'#bbb',border:'#555',surface:'#111',accent:'#a5c8eb'}:{text:'#191919',muted:'#505050',border:'#aaa',surface:'#fff',accent:'#214f7b'}
}
const number=(n:number)=>Number(n.toPrecision(6)).toString()
export function histogramOption(bins:HistogramBin[],theme:'dark'|'light'):ChartOption{
 const c=colours(theme)
 return {animation:false,backgroundColor:c.surface,textStyle:{fontFamily:'Arial',fontSize:16,color:c.text},aria:{enabled:true},
  grid:{left:85,right:30,top:30,bottom:110},
  tooltip:{trigger:'item',renderMode:'richText',formatter:(p:any)=>{const b=bins[p.dataIndex];return `Latency: ${number(b.lower)}–${number(b.upper)} ms\nPackets: ${b.count}\nDistribution: ${b.percentage.toFixed(3)}%`}},
  xAxis:{type:'category',name:'End-to-end latency (ms)',nameLocation:'middle',nameGap:75,axisLabel:{color:c.text,rotate:30,fontSize:14,interval:'auto'},axisLine:{lineStyle:{color:c.border}},data:bins.map(b=>`${number(b.lower)}–${number(b.upper)}`)},
  yAxis:{type:'value',name:'Distribution (%)',min:0,max:100,axisLabel:{color:c.text,formatter:'{value}%'},nameTextStyle:{color:c.text},splitLine:{lineStyle:{color:c.border,opacity:.35}}},
  series:[{type:'bar',name:'Packets',barCategoryGap:0,itemStyle:{color:c.accent},data:bins.map(b=>b.percentage)}]}
}
const palette=['#78b5e7','#e9a0a4','#91ce9c','#e2c16a','#b4a1df','#69c7c8','#e4a86f','#c5b082']
export function flowColour(flow:string){let hash=0;for(const ch of flow)hash=(hash*31+ch.charCodeAt(0))>>>0;return palette[hash%palette.length]}
export function segmentStyle(kind:Segment['kind'],flow:string,theme:'dark'|'light'){
 const c=colours(theme),color=flowColour(flow)
 return {fill:kind==='transmission'?color:c.surface,stroke:color,lineWidth:1,
  decal:kind==='transmission'?undefined:{symbol:'rect',color,backgroundColor:c.surface,symbolSize:1,rotation:Math.PI/4,dashArrayX:kind==='queueing'?[1,0]:[1,3],dashArrayY:kind==='queueing'?[1,4]:[1,3]}}
}
export function occupancyOption(segments:Segment[],flows:string[],theme:'dark'|'light',start:number,end:number,packetRows=false):ChartOption{
 const c=colours(theme)
 const rows=packetRows?[...new Set(segments.map(s=>s.packet))]:flows
 const index=new Map(rows.map((name,i)=>[name,i]))
 const kinds=['queueing','transmission','propagation'] as const
 const names={queueing:'Queueing',transmission:'Transmission',propagation:'Propagation (last bit)'}
 const series=kinds.map(kind=>({name:names[kind],type:'custom',dimensions:['row','start','end',{name:'flow',type:'ordinal'}],encode:{x:[1,2],y:0},clip:true,itemStyle:{color:kind==='transmission'?c.muted:c.surface,borderColor:c.muted,borderWidth:1,decal:kind==='transmission'?undefined:{symbol:'rect',color:c.muted,rotation:Math.PI/4,dashArrayX:kind==='queueing'?[1,0]:[1,3],dashArrayY:kind==='queueing'?[1,4]:[1,3]}},
  renderItem:(params:any,api:any)=>{
   const a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)])
   const height=Math.min(30,api.size([0,1])[1]*.56),area=params.coordSys
   const x=Math.max(a[0],area.x),right=Math.min(b[0],area.x+area.width)
   if(right<=x)return null
   return {type:'rect',shape:{x,y:a[1]-height/2,width:right-x,height},style:segmentStyle(kind,String(api.value(3)),theme)}
  },
  data:segments.filter(s=>s.kind===kind&&s.end>s.start).map(s=>{
   const color=flowColour(s.flow_id)
   const decal=kind==='transmission'?undefined:{symbol:'rect',color,backgroundColor:c.surface,symbolSize:1,rotation:Math.PI/4,dashArrayX:kind==='queueing'?[1,0]:[1,3],dashArrayY:kind==='queueing'?[1,4]:[1,3]}
   return {value:[index.get(packetRows?s.packet:s.flow_id)??0,s.start*1000,s.end*1000,s.flow_id],segment:s,itemStyle:{color:kind==='transmission'?color:c.surface,borderColor:color,borderWidth:1,decal,opacity:1}}
  })}))
 return {animation:false,backgroundColor:c.surface,textStyle:{fontFamily:'Arial',fontSize:16,color:c.text},aria:{enabled:true},
  grid:{left:155,right:60,top:60,bottom:100},legend:{top:6,textStyle:{color:c.text},selectedMode:true,data:kinds.map(k=>names[k])},
  tooltip:{trigger:'item',renderMode:'richText',formatter:(p:any)=>{const s=p.data.segment as Segment;return `${s.flow_id} · ${s.packet}\n${names[s.kind]}${s.censored?' (clipped at simulation end)':''}\nStart: ${number(s.start*1000)} ms\nEnd: ${number(s.end*1000)} ms\nDuration: ${number((s.end-s.start)*1000)} ms`}},
  xAxis:{type:'value',min:start*1000,max:end*1000,name:'Simulation time (ms)',nameLocation:'middle',nameGap:55,axisLabel:{color:c.text,formatter:(v:number)=>number(v)},splitLine:{lineStyle:{color:c.border,opacity:.3}}},
  yAxis:{type:'category',inverse:true,data:rows,axisLabel:{color:c.text,fontSize:16,width:135,overflow:'truncate'},axisTick:{show:false},splitLine:{show:true,lineStyle:{color:c.border,opacity:.2}}},
  dataZoom:[{type:'slider',xAxisIndex:0,bottom:25,filterMode:'weakFilter',textStyle:{color:c.text}},{type:'inside',xAxisIndex:0,filterMode:'weakFilter'},{type:'slider',yAxisIndex:0,right:12,startValue:0,endValue:Math.min(rows.length-1,11),filterMode:'weakFilter',show:rows.length>12}],series}
}
