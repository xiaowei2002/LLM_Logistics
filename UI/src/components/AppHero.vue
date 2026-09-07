<script setup>
defineProps({
  slogan: {
    type: String,
    default: '让 AI 驱动汽车物流的每一次决策',
  },
})
</script>

<template>
  <header class="hero">
    <div class="logo">
      物流<span class="logo-accent">智能体</span><span class="logo-dot"></span>
    </div>
    <p class="slogan">{{ slogan }}</p>
  </header>
</template>

<style scoped>
.hero {
  text-align: center;
  margin-bottom: 40px;
}

.logo {
  font-size: 46px;
  font-weight: 800;
  letter-spacing: 1px;
  display: inline-flex;
  align-items: baseline;
  position: relative;
}

.logo-accent {
  color: var(--primary);
}

.logo-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: conic-gradient(#ff6b6b, #feca57, #48dbfb, #1dd1a1, #ff6b6b);
  margin-left: 8px;
  align-self: flex-end;
  margin-bottom: 10px;
}

.slogan {
  color: var(--text-muted);
  margin-top: 12px;
  font-size: 15px;
}

@media (max-width: 620px) {
  .logo {
    font-size: 34px;
  }
}
</style>
