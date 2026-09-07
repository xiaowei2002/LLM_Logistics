/** 对话记录管理：多会话、切换、删除，持久化到 localStorage */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

const STORAGE_KEY = 'llm-logistics-chats'

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {
    // 存档损坏时忽略，重新开始
  }
  return null
}

function makeId() {
  return Date.now() + Math.random().toString(36).slice(2, 6)
}

export const useChatStore = defineStore('chat', () => {
  const saved = load()
  const conversations = ref(saved?.conversations ?? [])
  const currentId = ref(saved?.currentId ?? null)

  const current = computed(
    () => conversations.value.find((c) => c.id === currentId.value) ?? null,
  )

  function persist() {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ conversations: conversations.value, currentId: currentId.value }),
    )
  }

  function newChat() {
    const conv = { id: makeId(), title: '新对话', messages: [], updatedAt: Date.now() }
    conversations.value.unshift(conv)
    currentId.value = conv.id
    persist()
  }

  function selectChat(id) {
    currentId.value = id
    persist()
  }

  function deleteChat(id) {
    const idx = conversations.value.findIndex((c) => c.id === id)
    if (idx === -1) return
    conversations.value.splice(idx, 1)
    if (currentId.value === id) {
      currentId.value = conversations.value[0]?.id ?? null
    }
    persist()
  }

  return { conversations, currentId, current, newChat, selectChat, deleteChat, persist }
})
