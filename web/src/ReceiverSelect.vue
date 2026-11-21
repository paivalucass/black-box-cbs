<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
const props=defineProps<{id:string; modelValue:string[]; options:string[]}>()
const emit=defineEmits<{ 'update:modelValue':[value:string[]] }>()
const open=ref(false), container=ref<HTMLElement>(), trigger=ref<HTMLButtonElement>()
const caption=computed(()=>props.modelValue.length?props.modelValue.join(', '):'Choose receivers')
function toggle(value:string,checked:boolean){
 emit('update:modelValue',checked?[...props.modelValue,value]:props.modelValue.filter(n=>n!==value))
}
function outside(event:PointerEvent){if(!container.value?.contains(event.target as Node))open.value=false}
function leave(event:FocusEvent){if(!container.value?.contains(event.relatedTarget as Node))open.value=false}
function escape(){open.value=false;trigger.value?.focus()}
async function arrow(){open.value=true;await nextTick();container.value?.querySelector<HTMLInputElement>('input')?.focus()}
onMounted(()=>document.addEventListener('pointerdown',outside))
onUnmounted(()=>document.removeEventListener('pointerdown',outside))
</script>

<template>
 <div class="form-field">
  <span :id="id+'-label'">Receivers</span>
  <div ref="container" class="receiver-select" @keydown.esc.prevent.stop="escape" @focusout="leave">
   <button ref="trigger" type="button" class="dropdown-trigger" :aria-labelledby="id+'-label '+id+'-caption'" :aria-expanded="open" :aria-controls="id+'-options'" @click="open=!open" @keydown.down.prevent="arrow"><span :id="id+'-caption'">{{caption}}</span><span aria-hidden="true">▾</span></button>
   <div v-if="open" :id="id+'-options'" class="receiver-options" role="group" :aria-labelledby="id+'-label'">
    <p v-if="!options.length" class="muted">Add another end device to choose a receiver.</p>
    <label v-for="node in options" :key="node" class="check"><input type="checkbox" :checked="modelValue.includes(node)" @change="toggle(node,($event.target as HTMLInputElement).checked)">{{node}}</label>
    <button v-if="modelValue.length" type="button" @click="emit('update:modelValue',[])">Clear selection</button>
   </div>
  </div>
 </div>
</template>
