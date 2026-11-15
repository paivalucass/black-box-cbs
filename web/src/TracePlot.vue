<script setup lang="ts">
import { computed } from 'vue'
const props=defineProps<{samples:number[][]; label:string}>()
const points=computed(()=>{
 const valid=props.samples.filter(p=>p.every(Number.isFinite)); if(!valid.length)return ''
 const bounds=valid.reduce((b,p)=>[Math.min(b[0],p[0]),Math.max(b[1],p[0]),Math.min(b[2],p[1]),Math.max(b[3],p[1])],[Infinity,-Infinity,Infinity,-Infinity])
 const [x0,x1,y0,y1]=bounds, step=Math.max(1,Math.ceil(valid.length/5000))
 return valid.filter((_,i)=>i%step===0||i===valid.length-1).map(([x,y])=>`${35+(x-x0)/(x1-x0||1)*700},${180-(y-y0)/(y1-y0||1)*155}`).join(' ')

})
</script>
<template><figure><figcaption>{{label}}</figcaption><svg viewBox="0 0 760 210" role="img" :aria-label="label"><path d="M35 15V180H745" fill="none" stroke="#94a3b8"/><polyline :points="points" fill="none" stroke="#0d9488" stroke-width="2"/><text x="350" y="205" font-size="11">Time / independent variable</text></svg><p class="muted">{{samples.length}} samples · export artifacts for full numerical data</p></figure></template>
