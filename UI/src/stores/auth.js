import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import authConfig from '@/config/auth.json'

const { account, session } = authConfig

/** 读取本地登录态，过期或损坏时返回 null */
function readSession() {
  try {
    const raw = localStorage.getItem(session.storageKey)
    if (!raw) return null

    const data = JSON.parse(raw)
    if (!data?.username || Date.now() > data.expireAt) {
      localStorage.removeItem(session.storageKey)
      return null
    }
    return data
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const stored = readSession()

  /** 当前登录用户名，未登录为空 */
  const username = ref(stored?.username ?? '')
  /** 展示名称 */
  const displayName = ref(stored?.displayName ?? '')
  /** 登录错误提示 */
  const error = ref('')

  const isLoggedIn = computed(() => username.value !== '')

  /**
   * 单账号校验：账号信息来自 src/config/auth.json
   * @returns {boolean} 是否登录成功
   */
  function login(inputUsername, inputPassword) {
    error.value = ''

    if (!inputUsername.trim() || !inputPassword) {
      error.value = '请输入账号和密码'
      return false
    }

    if (inputUsername.trim() !== account.username || inputPassword !== account.password) {
      error.value = '账号或密码错误'
      return false
    }

    username.value = account.username
    displayName.value = account.displayName

    localStorage.setItem(
      session.storageKey,
      JSON.stringify({
        username: account.username,
        displayName: account.displayName,
        expireAt: Date.now() + session.expireHours * 60 * 60 * 1000,
      }),
    )

    return true
  }

  function logout() {
    username.value = ''
    displayName.value = ''
    error.value = ''
    localStorage.removeItem(session.storageKey)
  }

  return { username, displayName, error, isLoggedIn, login, logout }
})
