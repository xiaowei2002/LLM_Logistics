import { Cpu, Guide, Share, TrendCharts } from '@element-plus/icons-vue'

/* 四大决策模块：首页卡片与顶部导航共用；to 为空表示页面尚在建设中 */
export const decisionModules = [
  { key: 'forecast', title: '需求预测', en: 'Demand Forecasting', icon: TrendCharts, to: '/forecast' },
  { key: 'amr', title: 'AMR 集群调度', en: 'AMR Fleet Scheduling', icon: Guide, to: '' },
  {
    key: 'offloading',
    title: '计算卸载与调度可视化',
    en: 'Offloading & Scheduling',
    icon: Cpu,
    to: '/scheduling-offloading',
  },
  { key: 'graph', title: '行业知识图谱', en: 'Knowledge Graph', icon: Share, to: '/graph' },
]
