<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ option: { type: Object, required: true }, label: { type: String, required: true }, height: { type: Number, default: 220 } })
const host = ref(null)
let chart, observer
function render() {
  if (!host.value || !host.value.clientWidth || !host.value.clientHeight) return
  chart ||= echarts.init(host.value)
  chart.setOption({ ...props.option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches }, true)
  chart.resize()
}
onMounted(() => { observer = new ResizeObserver(render); observer.observe(host.value); render() })
watch(() => props.option, () => nextTick(render), { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>
<template><div ref="host" class="business-chart" role="img" :aria-label="label" :style="{ height: `${height}px` }"></div></template>
<style scoped>.business-chart{width:100%;min-width:0}</style>
