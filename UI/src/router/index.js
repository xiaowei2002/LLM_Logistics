import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import GraphView from '@/views/GraphView.vue'
import ForecastView from '@/views/ForecastView.vue'
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
      meta: { title: '物流智能体 - 汽车物流大模型系统', requiresAuth: true },
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
