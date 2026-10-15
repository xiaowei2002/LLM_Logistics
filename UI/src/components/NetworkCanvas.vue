<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

/* 首页背景：物流网络节点缓慢漂移，相近节点连线，"数据包"沿连线流动；鼠标附近的节点会连向指针 */
const LINK_DIST = 150
const POINTER_DIST = 190
const MAX_PACKETS = 16

const canvasRef = ref(null)
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

let ctx = null
let width = 0
let height = 0
let frameId = 0
let resizeFrame = 0
let nodes = []
let packets = []
const pointer = { x: -9999, y: -9999 }

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function resize() {
  const canvas = canvasRef.value
  if (!canvas) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  width = canvas.clientWidth
  height = canvas.clientHeight
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(height * dpr)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  const count = Math.min(90, Math.max(28, Math.round((width * height) / 15000)))
  nodes = Array.from({ length: count }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * 0.32,
    vy: (Math.random() - 0.5) * 0.32,
    r: Math.random() * 1.3 + 0.9,
    hub: Math.random() < 0.12,
  }))
  packets = []
  if (reducedMotion) draw()
}

function spawnPacket() {
  const from = nodes[Math.floor(Math.random() * nodes.length)]
  const near = nodes.filter((n) => n !== from && distance(from, n) < LINK_DIST)
  if (!near.length) return
  const to = near[Math.floor(Math.random() * near.length)]
  packets.push({ from, to, t: 0, speed: 0.006 + Math.random() * 0.008 })
}

function step() {
  for (const n of nodes) {
    n.x += n.vx
    n.y += n.vy
    if (n.x < 0 || n.x > width) n.vx *= -1
    if (n.y < 0 || n.y > height) n.vy *= -1
  }
  if (packets.length < MAX_PACKETS && Math.random() < 0.08) spawnPacket()
  // 走完或两端漂远（连线已断）的数据包移除
  packets = packets.filter((p) => (p.t += p.speed) < 1 && distance(p.from, p.to) < LINK_DIST * 1.2)
}

function draw() {
  ctx.clearRect(0, 0, width, height)
  ctx.lineWidth = 1

  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i]
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j]
      const d = distance(a, b)
      if (d < LINK_DIST) {
        ctx.strokeStyle = `rgba(94, 240, 207, ${(1 - d / LINK_DIST) * 0.22})`
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.lineTo(b.x, b.y)
        ctx.stroke()
      }
    }
    const dp = distance(a, pointer)
    if (dp < POINTER_DIST) {
      ctx.strokeStyle = `rgba(140, 200, 255, ${(1 - dp / POINTER_DIST) * 0.45})`
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(pointer.x, pointer.y)
      ctx.stroke()
    }
  }

  for (const n of nodes) {
    ctx.fillStyle = n.hub ? 'rgba(94, 240, 207, 0.9)' : 'rgba(170, 215, 235, 0.55)'
    ctx.beginPath()
    ctx.arc(n.x, n.y, n.hub ? n.r + 1.2 : n.r, 0, Math.PI * 2)
    ctx.fill()
    if (n.hub) {
      ctx.strokeStyle = 'rgba(94, 240, 207, 0.25)'
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2)
      ctx.stroke()
    }
  }

  ctx.save()
  ctx.shadowColor = '#2ce0b4'
  ctx.shadowBlur = 12
  for (const p of packets) {
    const x = p.from.x + (p.to.x - p.from.x) * p.t
    const y = p.from.y + (p.to.y - p.from.y) * p.t
    const tail = Math.max(0, p.t - 0.18)
    ctx.strokeStyle = 'rgba(94, 240, 207, 0.5)'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.moveTo(p.from.x + (p.to.x - p.from.x) * tail, p.from.y + (p.to.y - p.from.y) * tail)
    ctx.lineTo(x, y)
    ctx.stroke()
    ctx.fillStyle = '#c9fff1'
    ctx.beginPath()
    ctx.arc(x, y, 1.8, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()
}

function loop() {
  step()
  draw()
  frameId = requestAnimationFrame(loop)
}

function start() {
  if (!reducedMotion && !frameId) frameId = requestAnimationFrame(loop)
}

function stop() {
  cancelAnimationFrame(frameId)
  frameId = 0
}

function onResize() {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(resize)
}

function onPointerMove(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  pointer.x = e.clientX - rect.left
  pointer.y = e.clientY - rect.top
}

function onPointerLeave() {
  pointer.x = -9999
  pointer.y = -9999
}

/* 标签页不可见时暂停，省电 */
function onVisibility() {
  if (document.hidden) stop()
  else start()
}

onMounted(() => {
  ctx = canvasRef.value.getContext('2d')
  resize()
  start()
  window.addEventListener('resize', onResize)
  window.addEventListener('pointermove', onPointerMove)
  document.documentElement.addEventListener('pointerleave', onPointerLeave)
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  stop()
  cancelAnimationFrame(resizeFrame)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('pointermove', onPointerMove)
  document.documentElement.removeEventListener('pointerleave', onPointerLeave)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <canvas ref="canvasRef" class="network-canvas" aria-hidden="true"></canvas>
</template>

<style scoped>
.network-canvas {
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
