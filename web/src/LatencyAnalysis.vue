<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import EChart from './EChart.vue'
import { histogram, histogramOption } from './analysisCharts'
const props=defineProps<{metrics:any;theme:'dark'|'light'}>()
const flow=ref(''), binCount=ref(20)
const rows=computed(()=>Object.entries(props.metrics.flows) as [string,any][])
watch(rows,()=>{if(!props.metrics.flows[flow.value])flow.value=rows.value[0]?.[0]||''},{immediate:true})
const selected=computed(()=>props.metrics.flows[flow.value])
const delays=computed<number[]>(()=>selected.value?.latency_trace?.map((p:any)=>p.delay)||[])
const bins=computed(()=>histogram(delays.value,binCount.value))
const option=computed(()=>histogramOption(bins.value,props.theme))
const ms=(value:number|null|undefined)=>value==null?'—':(value*1000).toFixed(6)
</script>
<template>
 <section class="panel">
  <h2>End-to-end latency</h2>
  <p class="muted">Minimum, maximum and mean of received packets in the measurement cohort. All values are milliseconds. Multicast receivers are listed separately.</p>
  <div class="table-scroll"><table><thead><tr><th>Ethernet flow / receiver</th><th>Received samples</th><th>Min (ms)</th><th>Max (ms)</th><th>Mean (ms)</th></tr></thead><tbody>
   <tr v-for="[id,m] in rows" :key="id"><td><button @click="flow=id">{{id}}</button></td><td>{{m.latency_seconds.count}}</td><td>{{ms(m.latency_seconds.min)}}</td><td>{{ms(m.latency_seconds.max)}}</td><td>{{ms(m.latency_seconds.mean)}}</td></tr>
  </tbody></table></div>
  <h3>Packet latency distribution</h3>
  <div class="form-grid"><label>Ethernet flow / receiver<select v-model="flow"><option v-for="[id] in rows" :key="id" :value="id">{{id}}</option></select></label><label>Number of latency bins<input type="number" min="1" max="100" step="1" v-model.number="binCount"></label></div>
  <p class="muted">Each bar shows the percentage of received packets in its latency interval. The bars sum to 100%; this is a histogram, not a cumulative distribution.</p>
  <EChart v-if="bins.length" :option="option" :label="'Latency distribution — '+flow"/>
  <p v-else class="notice">No received packet samples are available for this flow. Latency is unavailable rather than zero.</p>
  <details><summary>Delivery and deadlines</summary><div class="table-scroll"><table><thead><tr><th>Flow / receiver</th><th>Generated</th><th>Received</th><th>Missing at horizon</th><th>Deadline misses / matured</th><th>Deadlines not reached</th></tr></thead><tbody><tr v-for="[id,m] in rows" :key="id"><td>{{id}}</td><td>{{m.generated}}</td><td>{{m.received}}</td><td>{{m.missing_at_horizon}}</td><td>{{m.deadline_misses}} / {{m.deadline_denominator}}</td><td>{{m.deadline_unmatured??'—'}}</td></tr></tbody></table></div></details>
 </section>
</template>
