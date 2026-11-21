<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { loadECharts } from './echarts'
import type { ChartInstance, ChartOption } from './echarts'
const props=withDefaults(defineProps<{option:ChartOption;label:string;height?:number}>(),{height:440})
const element=ref<HTMLElement>(), loading=ref(true), error=ref('')
let chart:ChartInstance|undefined, observer:ResizeObserver|undefined, disposed=false
async function initialize(){
 loading.value=true;error.value=''
 try{
  const echarts=await loadECharts()
  if(disposed||!element.value)return
  chart?.dispose();chart=echarts.init(element.value,undefined,{renderer:'svg'})
  chart.setOption(props.option,{notMerge:true})
 }catch{error.value='ECharts could not load. Connect to the chart CDN, or run npm run charts:install and rebuild for offline use.'}
 finally{if(!disposed)loading.value=false}
}
watch(()=>props.option,()=>chart?.setOption(props.option,{notMerge:true}))
watch(()=>props.height,async()=>{await nextTick();chart?.resize()})
function save(){
 if(!chart)return
 const link=document.createElement('a');link.href=chart.getDataURL({type:'svg'});link.download=props.label.replace(/[^a-zA-Z0-9_-]+/g,'-')+'.svg';link.click()
}
onMounted(()=>{initialize();if(element.value){observer=new ResizeObserver(()=>chart?.resize());observer.observe(element.value)}})
onUnmounted(()=>{disposed=true;observer?.disconnect();chart?.dispose()})
</script>
<template>
 <figure class="echart-figure">
  <figcaption>{{label}}</figcaption>
  <p v-if="loading" role="status">Loading ECharts…</p>
  <p v-if="error" role="alert" class="notice">{{error}} <button @click="initialize">Retry</button></p>
  <div v-show="!error" ref="element" class="echart" :style="{height:height+'px'}" role="img" :aria-label="label"></div>
  <button :disabled="loading||!!error" @click="save">Save chart as SVG</button>
 </figure>
</template>
