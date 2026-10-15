<script setup>
import { computed } from 'vue'
import forecastArt from '@/assets/illustrations/warehouse-scene.png'
import schedulingArt from '@/assets/illustrations/workshop-scene.png'

const props = defineProps({ mode: { type: String, default: 'forecast' } })
const artwork = computed(() => props.mode === 'scheduling' ? schedulingArt : forecastArt)
</script>

<template>
  <!-- 纯装饰插画沿用原来的左栏尺寸，不参与对话和结果展示。 -->
  <aside class="context-panel" aria-hidden="true">
    <img class="context-artwork" :src="artwork" alt="" draggable="false" />
  </aside>
</template>

<style scoped>
.context-panel {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  pointer-events: none;
  user-select: none;
  background: var(--bg, #eef0f5);
}

.context-artwork {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  opacity: 0.8;
  filter: saturate(0.72) contrast(0.94);
  /* 渐隐只作用于边缘，保留中央设备、货架和车辆的细节。 */
  mask-image: linear-gradient(to right, #000 60%, transparent 100%),
    linear-gradient(to bottom, transparent, #000 7%, #000 91%, transparent);
  mask-composite: intersect;
  -webkit-mask-image: linear-gradient(to right, #000 60%, transparent 100%),
    linear-gradient(to bottom, transparent, #000 7%, #000 91%, transparent);
  -webkit-mask-composite: source-in;
}
</style>
