import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import ChatView from '@/views/ChatView.vue'
import GraphView from '@/views/GraphView.vue'
import ForecastView from '@/views/ForecastView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import SchedulingOffloadingView from '@/views/SchedulingOffloadingView.vue'
import LoginView from '@/views/LoginView.vue'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { title: '登录 - 物流智能体' },
    },
    {
      path: '/',
      name: 'home',
      component: HomeView,
      meta: { title: '智慧物流决策系统 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/chat',
      name: 'chat',
      component: ChatView,
      meta: { title: '智能问答 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/graph',
      name: 'graph',
      component: GraphView,
      meta: { title: '知识图谱 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/forecast',
      name: 'forecast',
      component: ForecastView,
      meta: { title: '需求预测 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/scheduling-offloading',
      name: 'scheduling-offloading',
      component: SchedulingOffloadingView,
      meta: { title: '生产调度与计算卸载 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: KnowledgeView,
      meta: { title: '知识库管理 - 汽车物流大模型系统', requiresAuth: true },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// 单账号权限控制：未登录只能停留在登录页
router.beforeEach((to) => {
  const authStore = useAuthStore()

  if (to.meta.requiresAuth && !authStore.isLoggedIn) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }

  if (to.name === 'login' && authStore.isLoggedIn) {
    return { name: 'home' }
  }

  return true
})

router.afterEach((to) => {
  if (to.meta.title) {
    document.title = to.meta.title
  }
})

export default router
