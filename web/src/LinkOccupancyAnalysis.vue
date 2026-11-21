<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import EChart from './EChart.vue'
import { flowColour, occupancyOption } from './analysisCharts'
import type { Segment } from './analysisCharts'
const props=defineProps<{runId:string;metrics:any;theme:'dark'|'light'}>()
const key=ref(''), startMs=ref(0), endMs=ref(10), packetRows=ref(false)
const plottedStart=ref(0),plottedEnd=ref(.01)
const segments=ref<Segment[]>([]), loading=ref(false), error=ref(''), matching=ref(0), truncated=ref(false)
const metadata=computed(()=>props.metrics.link_occupancy)
const directions=computed<any[]>(()=>metadata.value?.directions||[])
const direction=computed(()=>directions.value.find(d=>d.key===key.value))
const flows=computed<string[]>(()=>direction.value?.flow_ids||[])
const horizon=computed(()=>Number(metadata.value?.observation_end||props.metrics.observation_end))
let request=0, controller:AbortController|undefined
function firstWindow(){const begin=direction.value?.first_time??0;startMs.value=begin*1000;endMs.value=Math.min(horizon.value,begin+.01)*1000}
async function load(){
 controller?.abort();controller=new AbortController();const current=++request
 segments.value=[];error.value='';truncated.value=false;matching.value=0
 if(!key.value){loading.value=false;return}
 if(!Number.isFinite(startMs.value)||!Number.isFinite(endMs.value)||startMs.value<0||endMs.value<=startMs.value||endMs.value>horizon.value*1000+1e-9){error.value='Choose a window within the simulation, with end after start.';loading.value=false;return}
 loading.value=true
 try{
  const params=new URLSearchParams({link:key.value,start:String(startMs.value/1000),end:String(endMs.value/1000),limit:'5000'})
  const response=await fetch('/api/jobs/'+encodeURIComponent(props.runId)+'/occupancy?'+params,{signal:controller.signal})
  const data=await response.json()
  if(!response.ok)throw new Error(data.error||'Could not load packet timing')
  if(current!==request)return
  segments.value=data.segments;plottedStart.value=data.start;plottedEnd.value=data.end;matching.value=data.total_matching;truncated.value=data.truncated
 }catch(e){if(current===request&&(e as Error).name!=='AbortError')error.value=String(e)}
 finally{if(current===request)loading.value=false}
}
watch(()=>props.runId,()=>{key.value='';segments.value=[]},{flush:'sync'})
watch(directions,()=>{key.value=directions.value.find(d=>d.available&&d.packet_count>0)?.key||directions.value.find(d=>d.available)?.key||''},{immediate:true})
watch(key,()=>{firstWindow();load()},{immediate:true})
const option=computed(()=>occupancyOption(segments.value,flows.value,props.theme,plottedStart.value,plottedEnd.value,packetRows.value))
const height=computed(()=>Math.min(850,Math.max(380,(packetRows.value?Math.min(new Set(segments.value.map(s=>s.packet)).size,12):Math.min(flows.value.length,12))*58+160)))
onUnmounted(()=>{request++;controller?.abort()})
</script>
<template>
 <section class="panel">
  <h2>Per link flow occupancy</h2>
  <p v-if="!metadata?.available" class="notice">{{metadata?.reason||'This run has no per-link packet timing. Enable packet timeline recording in Experiment and launch a new simulation.'}}</p>
  <template v-else>
   <details v-if="metadata.partial"><summary>Incomplete link recordings</summary><ul><li v-for="d in directions.filter(d=>!d.available)" :key="d.key">{{d.source}} → {{d.target}}: {{d.reason}}</li></ul></details>
   <div class="form-grid"><label>Link direction<select v-model="key"><option v-for="d in directions" :key="d.key" :value="d.key" :disabled="!d.available">{{d.source}} → {{d.target}} · {{d.link_id}} · {{d.packet_count}} packets{{d.available?'':' — unavailable'}}</option></select></label><label>Rows<select v-model="packetRows"><option :value="false">One row per flow</option><option :value="true">One row per packet</option></select></label><label>Window start (ms)<input type="number" min="0" step="0.001" v-model.number="startMs"></label><label>Window end (ms)<input type="number" min="0" step="0.001" v-model.number="endMs"></label></div>
   <div class="actions"><button :disabled="loading" @click="load">Load time window</button><button :disabled="loading" @click="firstWindow();load()">First activity</button><button :disabled="loading" @click="startMs=0;endMs=horizon*1000;load()">Full simulation</button></div>
   <p class="muted">Solid bars: transmission. Diagonal hatching: queueing. Dotted cross-pattern: propagation of the last bit. Use the zoom slider or mouse wheel to inspect packet timing. Packet rows separate overlapping queue waits.</p>
   <div class="flow-colour-legend"><span v-for="flow in flows" :key="flow"><i :style="{background:flowColour(flow)}"></i>{{flow}}</span></div>
   <p v-if="loading" role="status">Loading recorded packet intervals…</p>
   <p v-if="error" role="alert" class="notice">{{error}}</p>
   <p v-if="truncated" class="notice">This window contains {{matching}} intervals; only the first 5,000 are shown. Narrow the window to inspect every interval. No averaging or downsampling is applied.</p>
   <p v-if="!loading&&!error&&segments.length" class="muted">Displayed window: {{(plottedStart*1000).toPrecision(6)}}–{{(plottedEnd*1000).toPrecision(6)}} ms · {{segments.length}} recorded intervals.</p>
   <EChart v-if="!loading&&!error&&segments.length" :option="option" :height="height" :label="direction.source+' → '+direction.target+' — packet timeline'"/>
   <p v-else-if="!loading&&!error" class="muted">No recorded packet activity overlaps this window.</p>
   <p class="muted">{{metadata.coverage}}</p><p class="muted">{{metadata.semantics}} Inter-frame gaps and other delay stages remain visible as gaps, rather than being labelled queueing.</p>
  </template>
 </section>
</template>
