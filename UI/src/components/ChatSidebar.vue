<script setup>
import { Collection, Fold, SetUp, Share, SwitchButton, TrendCharts } from '@element-plus/icons-vue'
import IndustrialBackdrop from '@/components/IndustrialBackdrop.vue'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits(['logout', 'collapse', 'open-graph', 'open-forecast', 'open-knowledge', 'open-scheduling'])
const authStore = useAuthStore()
const features = [
  { label: '需求预测', icon: TrendCharts, event: 'open-forecast' },
  { label: '调度与计算卸载', icon: SetUp, event: 'open-scheduling' },
  { label: '知识库', icon: Collection, event: 'open-knowledge' },
  { label: '知识图谱', icon: Share, event: 'open-graph' },
]
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-logo">物流<span class="accent">智能体</span><span class="dot"></span></div>
      <el-tooltip content="收起边栏" placement="bottom" :show-after="300">
        <el-button class="icon-btn" circle text aria-label="收起边栏" @click="emit('collapse')"><el-icon><Fold /></el-icon></el-button>
      </el-tooltip>
    </div>

    <nav class="feature-nav" aria-label="功能导航">
      <button v-for="feature in features" :key="feature.event" type="button" class="feature-link" @click="emit(feature.event)">
        <el-icon><component :is="feature.icon" /></el-icon><span>{{ feature.label }}</span>
      </button>
    </nav>

    <div class="sidebar-scene"><IndustrialBackdrop mode="general" compact /><span>连接数据、生产与决策</span></div>
    <div class="sidebar-footer">
      <el-dropdown trigger="click" @command="emit('logout')">
        <span class="user-trigger">
          <el-avatar :size="28" class="user-avatar">{{ authStore.displayName.slice(0, 1) }}</el-avatar>
          <span class="user-name">{{ authStore.displayName }}</span>
        </span>
        <template #dropdown>
          <el-dropdown-menu><el-dropdown-item command="logout" :icon="SwitchButton">退出登录</el-dropdown-item></el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </aside>
</template>

<style scoped>
.sidebar{width:240px;flex-shrink:0;height:100%;background:#e8eaf0;display:flex;flex-direction:column;padding:16px 12px}
.sidebar-head{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:24px}
.sidebar-logo{font-size:18px;font-weight:800;letter-spacing:1px;display:inline-flex;align-items:baseline;color:var(--text);white-space:nowrap}
.sidebar-logo .accent{color:var(--primary)}
.sidebar-logo .dot{width:6px;height:6px;border-radius:50%;background:var(--primary);margin-left:5px;align-self:flex-end;margin-bottom:4px}
.icon-btn{color:var(--text-muted);flex-shrink:0}.icon-btn :deep(.el-icon){font-size:18px}
.icon-btn:hover{color:var(--text);background:#dde0e8}
.feature-nav{display:flex;flex:1;min-height:0;overflow-y:auto;flex-direction:column;gap:8px}
.feature-link{display:flex;align-items:center;gap:12px;width:100%;min-height:46px;padding:12px 14px;border:1px solid transparent;border-radius:10px;background:transparent;color:var(--text);font:inherit;font-size:14px;text-align:left;cursor:pointer;transition:background .15s,border-color .15s}
.feature-link .el-icon{font-size:19px;color:var(--primary);flex-shrink:0}
.feature-link:hover{background:var(--primary-light);border-color:#c8d6e2}
.feature-link:focus-visible{outline:2px solid var(--primary);outline-offset:-2px}
.sidebar-scene{position:relative;flex:none;padding:0 8px 13px;text-align:center;border-top:1px solid #d6dfe7}
.sidebar-scene :deep(.industrial-art){max-width:156px;margin:0 auto -4px}
.sidebar-scene>span{font-size:10px;letter-spacing:1px;color:#7891a3}
.sidebar-footer{display:flex;align-items:center;padding-top:12px;margin-top:8px;border-top:1px solid #dde0e8}
.user-trigger{display:inline-flex;align-items:center;gap:8px;cursor:pointer;padding:4px 6px;border-radius:8px;color:var(--text-muted);font-size:13px;outline:none}
.user-trigger:hover{background:#dde0e8;color:var(--text)}
.user-avatar{background:var(--primary);color:#fff;font-size:13px}
.user-name{max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-height:700px){.sidebar-scene{display:none}}
</style>
