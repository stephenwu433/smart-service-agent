from dataclasses import dataclass
from typing import Protocol

from smart_service_agent.models import Intent, KnowledgeReference


@dataclass(frozen=True)
class KnowledgeItem:
    knowledge_id: str
    keywords: tuple[str, ...]
    content: str
    source: str


class KnowledgeProvider(Protocol):
    """可替换的审核知识检索边界；provider 只返回足以直接回答的依据。"""

    def search(self, query: str, intent: Intent, limit: int = 3) -> list[KnowledgeReference]: ...


class InMemoryKnowledgeBase:
    """Small reviewed demo corpus behind a replaceable retrieval interface."""

    def __init__(self, version: str) -> None:
        self.version = version
        self._items = (
            KnowledgeItem(
                "KB-USAGE-001",
                ("第一次", "首次", "使用", "怎么用", "用法"),
                "首次使用充电设备前先核对额定输入、输出、接口协议和产品说明。",
                "安克创新赛演示知识库：充电设备使用指引（非官方政策）",
            ),
            KnowledgeItem(
                "KB-MODEL-001",
                ("型号", "接口", "功率", "协议", "充电器"),
                "选择充电设备时应同时核对设备型号、接口、充电协议与所需功率。",
                "安克创新赛演示知识库：充电设备选型指引（非官方政策）",
            ),
            KnowledgeItem(
                "KB-CHARGING-001",
                ("无法充电", "充不上电", "没反应", "充电中断", "充电不稳定"),
                "先只调整一个条件：使用已确认正常且参数匹配的充电线重试；观察设备是否开始稳定充电。无改善时进入下一步排查；出现异常发热、异味或鼓包时立即停止使用并转人工。",
                "安克创新赛演示知识库：无法充电排查指引（非官方政策）",
            ),
            KnowledgeItem(
                "KB-A1289-CHARGING-001",
                ("A1289", "737", "自充", "自身充电", "C1", "C2", "USB-A"),
                "A1289 自充必须使用 C1 接口；C2 和 USB-A 仅支持输出，不能用于自充。"
                "排查时先确认使用 C1 和随附 USB-C to USB-C 线，"
                "再保持充电器不变更换已知正常插座。"
                "每次只改变一个条件，并记录在哪个条件变化后恢复。",
                "A1289 比赛演示知识库：自充接口与排查（非官方政策）",
            ),
            KnowledgeItem(
                "KB-A1289-PORT-001",
                ("A1289", "接口", "C1", "C2", "USB-A", "输入", "输出"),
                "A1289 的 C1 支持输入和输出（输入最高 140W）；"
                "C2 仅输出；USB-A 仅输出（最高 18W）。",
                "A1289 比赛演示知识库：接口能力（非官方政策）",
            ),
        )

    def search(
        self, query: str, intent: Intent = Intent.CONSULT, limit: int = 3
    ) -> list[KnowledgeReference]:
        normalized = query.lower()
        if intent not in {Intent.USAGE, Intent.PURCHASE, Intent.CONSULT}:
            return []
        # 现有 demo 知识不覆盖拆机维修、电池更换或交易政策，不能仅因出现“使用”就作答。
        unsupported = ("退款", "退货", "订单", "拆机", "维修", "电池更换")
        if any(term in normalized for term in unsupported):
            return []
        ranked = [
            item
            for item in self._items
            if any(keyword.lower() in normalized for keyword in item.keywords)
        ]
        charging_terms = (
            "无法充电",
            "充不上电",
            "没反应",
            "充电中断",
            "充电不稳定",
            "自充",
            "A1289",
        )
        if any(term in normalized for term in charging_terms):
            ranked.sort(key=lambda item: item.knowledge_id != "KB-CHARGING-001")
        return [
            KnowledgeReference(
                knowledge_id=item.knowledge_id,
                version=self.version,
                excerpt=item.content,
                source=item.source,
            )
            for item in ranked[:limit]
        ]
