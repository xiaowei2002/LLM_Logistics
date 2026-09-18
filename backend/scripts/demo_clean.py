
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.agents.cleaning_agent import CleaningAgent

DEMO_CSV = BACKEND_ROOT / "demo_dirty_orders.csv"

# 模拟脏数据：重复行、缺失值、负数量、极端异常值、
# 物料编码/仓库大小写与空格不一致、日期格式混乱、仓库缺失
DIRTY_DATA = """\
order_no,sku,name,qty,created_at,warehouse
SO1001,SKU-001,轮胎,10,2026-09-01 08:30,WH-A
SO1001,SKU-001,轮胎,10,2026-09-01 08:30,WH-A
SO1002,SKU-002,机油,,2026-09-01,WH-A
SO1003, sku-001 ,轮胎,-5,2026/09/02,WH-A
SO1004,SKU-003,刹车片,3,2026-09-02,
SO1005,SKU-004,电瓶,99999,2026-09-02,WH-B
SO1006,SKU-002,机油 ,8,09-03-2026,WH-A
SO1007,SKU-005,雨刷,2,2026-09-03,wh-b
SO1008,SKU-001,轮胎,10,2026-09-03,WH-A
SO1009,SKU-006,车灯,1,2026-09-04,WH-C
SO1009,SKU-006,车灯,1,2026-09-04,WH-C
SO1010,SKU-007,滤芯,,2026-09-04,WH-A
SO1011,SKU-008,玻璃水,24,2026/9/5,WH-B
SO1012,SKU-003,刹车片,-2,2026-09-05,WH-A
SO1013,SKU-009,火花塞,4,2026-09-05,WH-C
SO1014,SKU-010,皮带,1,2026-09-06,
SO1015,SKU-011,冷却液,6,2026-09-06,WH-B
SO1016,SKU-012,空调滤,3,2026-09-07,WH-A
"""


async def main() -> None:
    DEMO_CSV.write_text(DIRTY_DATA, encoding="utf-8")
    print(f"[1/2] 已生成脏数据文件：{DEMO_CSV}\n")

    agent = CleaningAgent()
    instruction = (
        f"请清洗这份汽车物流出库明细数据：{DEMO_CSV}。"
        "先 read_metadata 做质量诊断，再用 run_pandas_code 执行：去重、缺失值处理、"
        "负数量/极端异常值处理、物料编码与仓库大小写/空格统一、日期格式统一。"
        "最后说明每一步影响多少行、清洗后还剩多少行。"
    )
    print("[2/2] 调用数据清洗智能体，等待结果...\n")
    result = await agent.clean(instruction)
    print("=" * 70)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
