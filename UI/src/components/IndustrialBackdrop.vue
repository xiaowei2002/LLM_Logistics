<script setup>
import { useId } from 'vue'
defineProps({ mode: { type: String, default: 'general' }, compact: Boolean })
const gradientId = `industrial-${useId().replace(/:/g, '')}`
</script>

<template>
  <div class="industrial-art" :class="{ compact }" aria-hidden="true">
    <svg viewBox="0 0 320 300" fill="none">
      <defs><linearGradient :id="gradientId" x1="25" y1="20" x2="285" y2="290" gradientUnits="userSpaceOnUse"><stop stop-color="#e6eff4"/><stop offset="1" stop-color="#f5f7fa"/></linearGradient></defs>
      <circle cx="160" cy="147" r="125" :fill="`url(#${gradientId})`"/>
      <g stroke="#cbd9e2" stroke-width="1"><path d="M30 232 160 157 290 232 160 307Z M30 203 160 128 290 203 M55 246 185 171 M82 261 212 186 M109 277 239 202" opacity=".55"/></g>
      <g v-if="mode === 'forecast'" stroke="#698a9d" stroke-width="1.7" stroke-linejoin="round">
        <path d="m62 101 79-45 70 40-79 46Z" fill="#dce7ee"/><path d="M62 101v114l70 40V142Z" fill="#eff4f7"/><path d="m132 142 79-46v114l-79 45Z" fill="#cbdde7"/>
        <path d="m73 133 48 28v24l-48-28Zm0 40 48 28v24l-48-28Z" fill="#9eb9c9"/>
        <path d="m143 151 55-32v25l-55 32Zm0 40 55-32v25l-55 32Z" fill="#f7fafc"/>
        <path d="m152 148 16-9v18l-16 9Zm24-14 16-9v18l-16 9Zm-24 54 16-9v18l-16 9Zm24-14 16-9v18l-16 9Z" fill="#94b7c8"/>
        <path d="m211 177 34-20 31 18-34 20Z" fill="#e1ebf1"/><path d="M211 177v36l31 18v-36Z" fill="#bfd3df"/><path d="m242 195 34-20v36l-34 20Z" fill="#f4f8fa"/>
        <path d="M45 96V52h68" stroke-dasharray="4 5" opacity=".6"/><path d="M226 128V76h47" stroke-dasharray="4 5" opacity=".6"/>
        <rect x="210" y="46" width="74" height="49" rx="7" fill="white" stroke="#b8ccd8"/><path d="m222 80 11-10 12 4 13-14 14 3" stroke="#308d94" stroke-width="2.8"/><circle cx="258" cy="60" r="3" fill="#308d94" stroke="none"/>
      </g>
      <g v-else-if="mode === 'scheduling'" stroke="#698a9d" stroke-width="1.7" stroke-linejoin="round">
        <path d="m47 157 57-33 57 33-57 33Z" fill="#e0ebf1"/><path d="M47 157v61l57 33v-61Z" fill="#b8cedb"/><path d="m104 190 57-33v61l-57 33Z" fill="#f2f6f9"/>
        <path d="m61 124 40-23 40 23-40 23Z" fill="#f8fafb"/><path d="M61 124v41l40 23v-41Z" fill="#d7e5ed"/><path d="m101 147 40-23v41l-40 23Z" fill="#a6c2d2"/>
        <path d="m111 151 20-12v21l-20 12Z" fill="#274e67"/><path d="M73 147v12m8-8v12m8-8v12" stroke="#308d94"/>
        <path d="m175 144 52-30 50 29-52 30Z" fill="#dbe8ef"/><path d="M175 144v73l50 29v-73Z" fill="#f2f6f9"/><path d="m225 173 52-30v73l-52 30Z" fill="#b1cad9"/>
        <path d="M183 164v13l32 18v-13Zm0 24v13l32 18v-13Z" fill="#c4d7e2"/><path d="m235 182 30-17m-30 29 30-17m-30 29 30-17" stroke="#6d96ad"/>
        <path d="m110 219 24-14 41 24 24-14" stroke="#308d94" stroke-dasharray="5 5" stroke-width="2"/>
        <path d="M146 75c0-9 8-16 17-15 4-13 24-15 31-3 15-3 28 8 26 21h-64c-5 0-10-1-10-3Z" fill="#e3eef3"/>
        <path d="M165 89v19l-30 18m60-37v19l30 12" stroke-dasharray="4 5"/>
      </g>
      <g v-else stroke="#698a9d" stroke-width="1.7" stroke-linejoin="round">
        <path d="m69 139 90-52 90 52-90 52Z" fill="#d5e4ed"/><path d="M69 139v61l90 52v-61Z" fill="#f3f7fa"/><path d="m159 191 90-52v61l-90 52Z" fill="#b8d0df"/>
        <path d="m92 158 18 10v29l-18-10Zm30 17 18 10v29l-18-10Z" fill="#9cbccc"/>
        <path d="m177 192 53-31v34l-53 31Z" fill="#eaf2f6"/>
        <path d="m84 121 35-20v-27l40 23V67l62 36v28" fill="#e9f1f5"/>
        <path d="M107 87V60m12 34V66" stroke-width="6" stroke="#afc9d8"/>
        <path d="M56 86h26m159 3h29m-110-49v20" stroke-dasharray="4 5"/>
        <circle cx="47" cy="86" r="9" fill="#f8fbfc"/><circle cx="278" cy="89" r="9" fill="#e4f1f0" stroke="#308d94"/><circle cx="160" cy="31" r="9" fill="#f8fbfc"/>
      </g>
      <g fill="#308d94"><circle cx="39" cy="189" r="3"/><circle cx="284" cy="132" r="3"/><circle cx="243" cy="263" r="3"/></g>
    </svg>
  </div>
</template>

<style scoped>
.industrial-art{width:100%;max-width:320px;margin:auto;pointer-events:none}.industrial-art svg{display:block;width:100%;height:auto}.industrial-art.compact{max-width:190px;opacity:.85}
</style>
