<script setup>
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Collection, Right, SwitchButton, Tools } from '@element-plus/icons-vue'

import NetworkCanvas from '@/components/NetworkCanvas.vue'
import SettingsDialog from '@/components/SettingsDialog.vue'
import { decisionModules } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const settingsOpen = ref(false)

function handleCommand(command) {
  if (command === 'settings') {
    settingsOpen.value = true
  } else if (command === 'logout') {
    authStore.logout()
    router.replace({ name: 'login' })
  }
}

/* 卡片跟随指针：聚光位置 + 轻微 3D 倾斜 */
function trackPointer(e) {
  const el = e.currentTarget
  const rect = el.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  el.style.setProperty('--mx', `${x}px`)
  el.style.setProperty('--my', `${y}px`)
  el.style.setProperty('--ry', `${((x / rect.width) - 0.5) * 8}deg`)
  el.style.setProperty('--rx', `${(0.5 - y / rect.height) * 8}deg`)
}

function resetTilt(e) {
  e.currentTarget.style.setProperty('--rx', '0deg')
  e.currentTarget.style.setProperty('--ry', '0deg')
}

function notifySoon(mod) {
  ElMessage.info(`${mod.title}模块建设中，敬请期待`)
}
</script>

<template>
  <div class="portal">
    <!-- 动态背景：光晕 + 物流网络 + 透视地面网格 -->
    <div class="portal-bg" aria-hidden="true">
      <span class="glow glow-a"></span>
      <span class="glow glow-b"></span>
      <NetworkCanvas class="portal-network" />
      <div class="portal-floor"><div class="floor-grid"></div></div>
    </div>

    <header class="portal-top">
      <RouterLink to="/knowledge" class="top-link"><el-icon><Collection /></el-icon><span>知识库</span></RouterLink>
      <el-dropdown trigger="click" @command="handleCommand">
        <button type="button" class="top-user" :aria-label="`用户菜单：${authStore.displayName}`">
          <el-avatar :size="30" class="top-avatar">{{ authStore.displayName.slice(0, 1) }}</el-avatar>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="settings" :icon="Tools">系统设置</el-dropdown-item>
            <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>
    <SettingsDialog v-model:open="settingsOpen" />

    <main class="portal-center">
      <h1 class="title">智慧物流决策系统</h1>
      <p class="subtitle"><i></i>INTELLIGENT LOGISTICS DECISION SYSTEM<i></i></p>

      <div class="module-grid">
        <div v-for="(mod, index) in decisionModules" :key="mod.key" class="card-wrap" :style="{ '--i': index }">
          <component
            :is="mod.to ? RouterLink : 'button'"
            :to="mod.to || undefined"
            :type="mod.to ? undefined : 'button'"
            class="module-card"
            :class="{ soon: !mod.to }"
            @pointermove="trackPointer"
            @pointerleave="resetTilt"
            @click="mod.to || notifySoon(mod)"
          >
            <span class="card-border" aria-hidden="true"></span>
            <span class="card-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="card-icon"><el-icon><component :is="mod.icon" /></el-icon></span>
            <span class="card-title">{{ mod.title }}</span>
            <span class="card-en">{{ mod.en }}</span>
            <span v-if="mod.to" class="card-foot">进入模块<el-icon><Right /></el-icon></span>
            <span v-else class="card-foot soon-tag">建设中</span>
          </component>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* 卡片边框流光的旋转角度（需注册为可动画的自定义属性） */
@property --angle {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

.portal {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  overflow: hidden;
  color: #fff;
  background:
    radial-gradient(1100px 560px at 50% -12%, #0b4b6e 0%, transparent 62%),
    linear-gradient(180deg, #00121e 0%, #001f32 52%, #00172a 100%);
}

/* ===== 动态背景 ===== */
.portal-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.portal-network {
  position: absolute;
  inset: 0;
}

.glow {
  position: absolute;
  width: 560px;
  height: 560px;
  border-radius: 50%;
  filter: blur(30px);
  animation: drift 18s ease-in-out infinite alternate;
}

.glow-a {
  top: 6%;
  left: -160px;
  background: radial-gradient(circle, rgba(44, 224, 180, 0.2), transparent 68%);
}

.glow-b {
  right: -180px;
  bottom: 4%;
  background: radial-gradient(circle, rgba(70, 140, 255, 0.18), transparent 68%);
  animation-duration: 22s;
  animation-direction: alternate-reverse;
}

@keyframes drift {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to { transform: translate3d(120px, 60px, 0) scale(1.15); }
}

/* 透视地面：网格向观察者缓慢移动 */
.portal-floor {
  position: absolute;
  left: -40%;
  right: -40%;
  bottom: 0;
  height: 42vh;
  perspective: 420px;
  perspective-origin: 50% 0;
  -webkit-mask-image: linear-gradient(to top, #000 0%, transparent 85%);
  mask-image: linear-gradient(to top, #000 0%, transparent 85%);
}

.floor-grid {
  position: absolute;
  inset: 0 0 -60% 0;
  transform-origin: 50% 0;
  transform: rotateX(62deg);
  background-image:
    linear-gradient(rgba(94, 240, 207, 0.18) 1px, transparent 1px),
    linear-gradient(90deg, rgba(94, 240, 207, 0.18) 1px, transparent 1px);
  background-size: 64px 64px;
  animation: floor-move 3.2s linear infinite;
}

@keyframes floor-move {
  to { background-position: 0 64px; }
}

/* ===== 右上角工具 ===== */
.portal-top {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  padding: 18px 28px 0;
  animation: fade-down 0.8s 1.1s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.top-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-radius: 8px;
  color: #a9c2d2;
  font-size: 13.5px;
  text-decoration: none;
  transition: color 0.2s, background 0.2s;
}

.top-link:hover,
.top-link:focus-visible {
  outline: none;
  color: #fff;
  background: #ffffff14;
}

.top-user {
  display: inline-flex;
  margin-left: 8px;
  padding: 2px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.top-user:hover,
.top-user:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px #2ce0b4;
}

.top-avatar {
  background: #2ce0b4;
  color: var(--primary);
  font-weight: 700;
}

@keyframes fade-down {
  from { opacity: 0; transform: translateY(-12px); }
  to { opacity: 1; transform: none; }
}

/* ===== 中央：标题 + 模块 ===== */
.portal-center {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 32px 88px;
}

.title {
  /* letter-spacing 会在末字后留白，用等量 padding-left 抵消，保证视觉居中 */
  padding-left: 0.2em;
  font-size: clamp(34px, 5.4vw, 64px);
  font-weight: 800;
  letter-spacing: 0.2em;
  line-height: 1.2;
  text-align: center;
  color: transparent;
  background-image:
    linear-gradient(110deg, transparent 38%, rgba(255, 255, 255, 0.95) 50%, transparent 62%),
    linear-gradient(180deg, #ffffff 25%, #a8f0df 100%);
  background-size: 260% 100%, 100% 100%;
  background-position: 130% 0, 0 0;
  background-repeat: no-repeat;
  -webkit-background-clip: text;
  background-clip: text;
  filter: drop-shadow(0 6px 30px rgba(44, 224, 180, 0.25));
  animation:
    title-in 1.3s cubic-bezier(0.2, 0.8, 0.2, 1) both,
    shimmer 6s 1.6s ease-in-out infinite;
}

@keyframes title-in {
  from {
    opacity: 0;
    letter-spacing: 0.7em;
    padding-left: 0.7em;
    filter: blur(10px) drop-shadow(0 6px 30px rgba(44, 224, 180, 0));
  }
  to {
    opacity: 1;
    letter-spacing: 0.2em;
    padding-left: 0.2em;
    filter: blur(0) drop-shadow(0 6px 30px rgba(44, 224, 180, 0.25));
  }
}

@keyframes shimmer {
  0% { background-position: 130% 0, 0 0; }
  45%, 100% { background-position: -30% 0, 0 0; }
}

.subtitle {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
  color: #7fa3b8;
  font-size: clamp(10px, 1.1vw, 13px);
  letter-spacing: 0.32em;
  white-space: nowrap;
  animation: fade-up 0.9s 0.7s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.subtitle i {
  width: 56px;
  height: 1px;
  background: linear-gradient(90deg, transparent, #2ce0b4);
  transform-origin: right;
  animation: line-in 0.9s 1s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

.subtitle i:last-child {
  background: linear-gradient(270deg, transparent, #2ce0b4);
  transform-origin: left;
}

@keyframes line-in {
  from { transform: scaleX(0); }
  to { transform: scaleX(1); }
}

@keyframes fade-up {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: none; }
}

/* ===== 模块卡片 ===== */
.module-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 22px;
  width: 100%;
  max-width: 1140px;
  margin-top: 64px;
}

.card-wrap {
  animation: card-in 0.85s cubic-bezier(0.2, 0.8, 0.2, 1) both;
  animation-delay: calc(0.9s + var(--i) * 0.12s);
}

@keyframes card-in {
  from { opacity: 0; transform: translateY(40px) scale(0.96); }
  to { opacity: 1; transform: none; }
}

.module-card {
  --mx: 50%;
  --my: 50%;
  --rx: 0deg;
  --ry: 0deg;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  height: 100%;
  min-height: 232px;
  padding: 28px 24px 22px;
  border: 1px solid rgba(148, 214, 230, 0.16);
  border-radius: 18px;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.09), rgba(255, 255, 255, 0.02));
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
  color: #fff;
  font: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  overflow: hidden;
  transform: perspective(900px) rotateX(var(--rx)) rotateY(var(--ry));
  transition:
    transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1),
    border-color 0.35s,
    box-shadow 0.35s,
    background 0.35s;
}

/* 指针聚光 */
.module-card::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(260px circle at var(--mx) var(--my), rgba(44, 224, 180, 0.16), transparent 65%);
  opacity: 0;
  transition: opacity 0.35s;
  pointer-events: none;
}

.module-card:hover,
.module-card:focus-visible {
  outline: none;
  transform: perspective(900px) rotateX(var(--rx)) rotateY(var(--ry)) translateY(-10px);
  border-color: rgba(44, 224, 180, 0.45);
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.12), rgba(255, 255, 255, 0.03));
  box-shadow:
    0 24px 48px rgba(0, 0, 0, 0.38),
    0 0 36px rgba(44, 224, 180, 0.16);
}

.module-card:hover::after,
.module-card:focus-visible::after {
  opacity: 1;
}

/* 悬停时沿边框旋转的流光 */
.card-border {
  position: absolute;
  inset: 0;
  padding: 1.5px;
  border-radius: inherit;
  background: conic-gradient(from var(--angle), transparent 0deg, #2ce0b4 70deg, #7ab8ff 110deg, transparent 160deg);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  opacity: 0;
  transition: opacity 0.35s;
  animation: border-spin 3.2s linear infinite;
  pointer-events: none;
}

.module-card:hover .card-border,
.module-card:focus-visible .card-border {
  opacity: 1;
}

@keyframes border-spin {
  to { --angle: 360deg; }
}

.card-index {
  position: absolute;
  top: 24px;
  right: 24px;
  color: rgba(255, 255, 255, 0.22);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 2px;
  transition: color 0.35s;
}

.module-card:hover .card-index {
  color: #2ce0b4;
}

.card-icon {
  position: relative;
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  border: 1px solid rgba(44, 224, 180, 0.35);
  border-radius: 16px;
  background: rgba(44, 224, 180, 0.1);
  color: #5ef0cf;
  font-size: 27px;
  transition: transform 0.45s cubic-bezier(0.2, 0.8, 0.2, 1), background 0.35s, color 0.35s, box-shadow 0.35s;
}

/* 图标外扩散的光环 */
.card-icon::after {
  content: '';
  position: absolute;
  inset: -1px;
  border: 1px solid #2ce0b4;
  border-radius: inherit;
  opacity: 0;
}

.module-card:hover .card-icon {
  transform: scale(1.08) rotate(-6deg);
  background: #2ce0b4;
  color: #002b43;
  box-shadow: 0 0 28px rgba(44, 224, 180, 0.55);
}

.module-card:hover .card-icon::after {
  animation: ripple 1.6s ease-out infinite;
}

@keyframes ripple {
  from { opacity: 0.8; transform: scale(1); }
  to { opacity: 0; transform: scale(1.6); }
}

.card-title {
  margin-top: 24px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 1px;
  line-height: 1.4;
}

.card-en {
  margin-top: 6px;
  color: rgba(190, 220, 232, 0.5);
  font-size: 11.5px;
  letter-spacing: 1.2px;
  text-transform: uppercase;
}

.card-foot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: auto;
  padding-top: 22px;
  color: #2ce0b4;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
}

.card-foot .el-icon {
  transition: transform 0.35s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.module-card:hover .card-foot .el-icon {
  transform: translateX(6px);
}

/* 建设中：虚线边框、弱化色彩，悬停只轻微上浮 */
.module-card.soon {
  border-style: dashed;
  border-color: rgba(148, 214, 230, 0.2);
  background: rgba(255, 255, 255, 0.03);
}

.module-card.soon .card-icon {
  border-color: rgba(148, 214, 230, 0.2);
  background: rgba(255, 255, 255, 0.04);
  color: #7f97a8;
}

.module-card.soon .card-title {
  color: #c4d3dd;
}

.module-card.soon:hover {
  transform: perspective(900px) translateY(-4px);
  border-color: rgba(245, 196, 110, 0.4);
  box-shadow: 0 16px 32px rgba(0, 0, 0, 0.3);
}

.module-card.soon:hover .card-border,
.module-card.soon:hover::after {
  opacity: 0;
}

.module-card.soon:hover .card-icon {
  transform: none;
  background: rgba(255, 255, 255, 0.06);
  color: #a9bccb;
  box-shadow: none;
}

.module-card.soon:hover .card-icon::after {
  animation: none;
}

.module-card.soon:hover .card-index {
  color: rgba(255, 255, 255, 0.22);
}

.soon-tag {
  padding: 3px 12px;
  border-radius: 999px;
  background: rgba(245, 181, 74, 0.14);
  color: #f5c46e;
  font-size: 12px;
}

/* ===== 响应式 ===== */
@media (max-width: 1024px) {
  .module-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-width: 680px;
  }

  .module-card {
    min-height: 210px;
  }
}

@media (max-width: 560px) {
  .portal-top {
    padding: 12px 12px 0;
  }

  .top-link span {
    display: none;
  }

  .portal-center {
    justify-content: flex-start;
    padding: 40px 16px 48px;
  }

  .subtitle {
    gap: 8px;
    letter-spacing: 0.16em;
  }

  .subtitle i {
    width: 24px;
  }

  .module-grid {
    grid-template-columns: 1fr;
    gap: 14px;
    margin-top: 40px;
  }

  .module-card {
    min-height: 0;
    padding: 22px 20px 18px;
  }

  .card-title {
    margin-top: 16px;
  }

  .card-foot {
    padding-top: 16px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .glow,
  .floor-grid,
  .portal-top,
  .title,
  .subtitle,
  .subtitle i,
  .card-wrap,
  .card-border,
  .module-card:hover .card-icon::after {
    animation: none;
  }

  .module-card,
  .module-card:hover {
    transform: none;
  }
}
</style>
