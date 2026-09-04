// ===== 首页交互（JoyAgent 风格）=====

const MODE_INFO = {
  forecast: { name: '需求预测', hint: '描述你的预测需求，需求预测 Agent 将输出未来需求趋势与结果表格。' },
  schedule: { name: '生产调度', hint: '输入订单与产线信息，生产调度 Agent 将生成最优排程方案。' },
  offload:  { name: '计算卸载', hint: '描述任务与网络环境，计算卸载 Agent 将给出端-边-云卸载决策。' },
};

const modeItems = document.querySelectorAll('.mode-item');
const chipText = document.getElementById('chip-text');
const modeHint = document.getElementById('mode-hint');
const taskInput = document.getElementById('task-input');
const sendBtn = document.getElementById('send-btn');
const deepBtn = document.getElementById('deep-btn');

let currentMode = 'forecast';

// 切换模式
modeItems.forEach((btn) => {
  btn.addEventListener('click', () => {
    modeItems.forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    currentMode = btn.dataset.mode;
    chipText.textContent = MODE_INFO[currentMode].name;
    modeHint.textContent = MODE_INFO[currentMode].hint;
    taskInput.focus();
  });
});

// 深度思考开关
deepBtn.addEventListener('click', () => {
  deepBtn.classList.toggle('active');
});

// 发送（当前仅演示，后续接入后端 Agent）
function sendTask() {
  const text = taskInput.value.trim();
  if (!text) {
    taskInput.focus();
    return;
  }
  // TODO: 接入后端 API，将 text + currentMode 发给对应 Agent
  alert(`【演示】模式：${MODE_INFO[currentMode].name}\n任务：${text}\n\n（Agent 算法封装好后，这里将调用后端接口并展示结果）`);
  taskInput.value = '';
}

sendBtn.addEventListener('click', sendTask);

taskInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendTask();
  }
});

// 点击能力卡片 = 切换到对应模式并聚焦输入框
document.querySelectorAll('.case-card').forEach((card) => {
  card.addEventListener('click', () => {
    const mode = card.dataset.mode;
    const btn = document.querySelector(`.mode-item[data-mode="${mode}"]`);
    if (btn) btn.click();
  });
});
