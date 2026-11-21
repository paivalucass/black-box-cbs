<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import type { Flow, Scenario } from './types'
const props=defineProps<{scenario:Scenario; flow:Flow; receiver:string; id:string}>()
const emit=defineEmits<{change:[path:string[]|null]}>()
const paths=ref<string[][]>([]), defaultPath=ref<string[]|null>(null), savedPath=ref<string[]|null>(null)
const hasMore=ref(false), unavailable=ref(false), loading=ref(false), error=ref('')
let offset=0, generation=0, controller:AbortController|undefined
const selected=computed(()=>props.flow.routes[props.receiver]?JSON.stringify(props.flow.routes[props.receiver]):'')
const options=computed(()=>{
 const list=[...paths.value]
 if(savedPath.value&&!list.some(p=>JSON.stringify(p)===JSON.stringify(savedPath.value)))list.push(savedPath.value)
 return list
})
const selectionUnavailable=computed(()=>unavailable.value&&!!selected.value&&!options.value.some(path=>JSON.stringify(path)===selected.value))
const fingerprint=computed(()=>JSON.stringify({
 nodes:props.scenario.nodes.map(n=>[n.id,n.kind,n.ports.map(p=>p.vlans)]),
 links:props.scenario.links.map(l=>[l.a.node,l.b.node]),
 flow:props.flow.id,source:props.flow.source,vlan:props.flow.vlan,receivers:props.flow.receivers,mac:props.flow.multicast_mac,receiver:props.receiver
}))
async function load(reset=false){
 controller?.abort();controller=new AbortController()
 const request=++generation
 if(reset){paths.value=[];offset=0;defaultPath.value=null;savedPath.value=null;hasMore.value=false;unavailable.value=false}
 loading.value=true;error.value=''
 try{
  const response=await fetch('/api/routes',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({scenario:{...props.scenario,name:props.scenario.name.trim()||'Route preview'},flow_id:props.flow.id,receiver:props.receiver,offset,limit:25})})
  const data=await response.json()
  if(!response.ok)throw new Error(data.error||'Could not list routes')
  if(request!==generation)return
  paths.value.push(...data.paths);offset+=data.paths.length
  defaultPath.value=data.default_path;savedPath.value=data.saved_path
  unavailable.value=data.saved_unavailable;hasMore.value=data.has_more
 }catch(e){if(request===generation&&(e as Error).name!=='AbortError')error.value=String(e)}
 finally{if(request===generation)loading.value=false}
}
watch(fingerprint,()=>load(true),{immediate:true})
onUnmounted(()=>{generation++;controller?.abort()})
function change(event:Event){const value=(event.target as HTMLSelectElement).value;emit('change',value?JSON.parse(value):null)}
</script>

<template>
 <div class="route-field">
  <label :for="id">Route to {{receiver}}</label>
  <select :id="id" :value="selected" :disabled="loading&&!paths.length" @change="change">
   <option value="">Shortest path (automatic){{defaultPath?' — '+defaultPath.join(' → '):loading?' — loading…':' — no available route'}}</option>
   <option v-for="path in options" :key="JSON.stringify(path)" :value="JSON.stringify(path)">{{path.join(' → ')}} ({{path.length-1}} links)</option>
   <option v-if="selectionUnavailable" :value="selected" disabled>Previously selected route is unavailable — choose another</option>
  </select>
  <p v-if="error" role="alert" class="notice">{{error}}</p>
  <p v-else-if="selectionUnavailable" class="notice">The saved route no longer matches the topology or VLAN. Choose an available route.</p>
  <p v-else-if="!loading&&!defaultPath" class="muted">No route permits this flow's VLAN between the selected devices.</p>
  <button v-if="hasMore||error" type="button" :disabled="loading" @click="load(!!error)">{{loading?'Loading…':error?'Retry route lookup':'Load more routes'}}</button>
 </div>
</template>
