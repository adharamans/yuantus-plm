import random
import uuid
from typing import List, Dict, Optional
from datetime import datetime
import faker

from sqlalchemy.orm import Session
from yuantus.meta_engine.models.item import Item
from yuantus.security.rbac.models import RBACUser

fake = faker.Faker()

class DataSeeder:
    def __init__(self, session: Session, user_id: int = 1):
        self.session = session
        self.user_id = user_id
        self.created_parts: List[Item] = []
        self.created_docs: List[Item] = []

    def _create_item(
        self,
        item_type_id: str,
        properties: Dict,
        source_id: str = None,
        related_id: str = None
    ) -> Item:
        item_id = str(uuid.uuid4())
        # 对于关系对象，通常不需要独立的 config_id 版本链，但在统一模型下也生成一个
        config_id = str(uuid.uuid4())

        item = Item(
            id=item_id,
            item_type_id=item_type_id,
            config_id=config_id,
            generation=1,
            is_current=True,
            state="Draft",
            current_state=None, # 简化处理，暂不关联 lifecycle state id
            created_by_id=self.user_id,
            modified_by_id=self.user_id,
            owner_id=self.user_id,
            permission_id="Default", # 假设 seed-meta 创建了 Default 权限
            source_id=source_id,
            related_id=related_id,
            properties=properties
        )
        self.session.add(item)
        return item

    def generate_parts(self, count: int = 50, prefix: str = "P-") -> List[Item]:
        """生成基础零件数据"""
        print(f"Generating {count} parts...")

        part_adjectives = ["Titanium", "Steel", "Aluminum", "Carbon", "Plastic", "Rubber", "Composite"]
        part_nouns = ["Bracket", "Screw", "Bolt", "Nut", "Panel", "Lever", "Gear", "Shaft", "Spring", "Housing", "Cover", "Base"]

        start_idx = len(self.created_parts) + 10000

        new_parts = []
        for i in range(count):
            name = f"{random.choice(part_adjectives)} {random.choice(part_nouns)}"
            item_number = f"{prefix}{start_idx + i}"

            props = {
                "item_number": item_number,
                "name": name,
                "description": fake.sentence(),
                "revision": "A",
                "state": "Draft",
                "cost": round(random.uniform(1.0, 500.0), 2),
                "weight": round(random.uniform(0.1, 50.0), 3)
            }

            part = self._create_item("Part", props)
            new_parts.append(part)

        self.session.flush()
        self.created_parts.extend(new_parts)
        return new_parts

    def generate_documents(self, count: int = 20, prefix: str = "D-") -> List[Item]:
        """生成文档并随机关联到现有零件"""
        if not self.created_parts:
            print("Warning: No parts available to link documents to.")

        print(f"Generating {count} documents...")
        start_idx = len(self.created_docs) + 20000

        new_docs = []
        for i in range(count):
            item_number = f"{prefix}{start_idx + i}"
            title = fake.catch_phrase()

            props = {
                "doc_number": item_number,
                "name": title,
                "description": fake.text(max_nb_chars=100),
                "state": "Draft"
            }

            doc = self._create_item("Document", props)
            new_docs.append(doc)

            # 随机关联到一个 Part (Part -> Document 关系，假设名为 Part Document)
            # 注意：seed-meta 中可能没定义 Part Document 关系，这里暂时跳过物理关联，
            # 仅创建文档对象。如果需要关联，需确认 Meta 定义。

        self.session.flush()
        self.created_docs.extend(new_docs)
        return new_docs

    def build_simple_bom(self, root_count: int = 5, depth: int = 3, children_per_node: int = 3):
        """
        构建 BOM 结构
        策略:
        1. 从 created_parts 中选出 root_count 个作为成品(Roots)
        2. 递归地为它们分配子件
        """
        if len(self.created_parts) < root_count + (root_count * children_per_node):
            print("Not enough parts to build meaningful BOM. Generating more...")
            needed = (root_count * children_per_node * depth) - len(self.created_parts) + 100
            self.generate_parts(max(needed, 50))

        # 随机打乱 parts 以便随机选取
        pool = list(self.created_parts)
        random.shuffle(pool)

        roots = []
        # 选取 Roots
        for _ in range(root_count):
            if pool:
                roots.append(pool.pop())

        print(f"Building BOM structures for {len(roots)} roots, depth={depth}...")

        relationships_count = 0

        def add_children(parent: Item, current_depth: int):
            nonlocal relationships_count
            if current_depth >= depth or not pool:
                return

            # 决定这个节点有几个子件 (1 到 children_per_node)
            num_children = random.randint(1, children_per_node)

            for _ in range(num_children):
                if not pool:
                    break

                child = pool.pop()

                # 创建 BOM 关系 (Part BOM)
                props = {
                    "quantity": float(random.randint(1, 10)),
                    "find_num": str(random.randint(10, 100))
                }

                self._create_item(
                    item_type_id="Part BOM",
                    properties=props,
                    source_id=parent.id,
                    related_id=child.id
                )
                relationships_count += 1

                # 递归
                add_children(child, current_depth + 1)

        for root in roots:
            add_children(root, 0)
            # 标记 Root 为 Released (模拟成品)
            root.state = "Released"
            if root.properties:
                root.properties['state'] = "Released"
                self.session.add(root) # Update

        self.session.flush()
        print(f"BOM generation complete. Created {relationships_count} relationships.")

def run_seed(
    session: Session,
    part_count: int = 100,
    doc_count: int = 50,
    bom_roots: int = 10,
    bom_depth: int = 3
):
    seeder = DataSeeder(session)
    seeder.generate_parts(part_count)
    seeder.generate_documents(doc_count)
    seeder.build_simple_bom(root_count=bom_roots, depth=bom_depth)
    session.commit()
    print("Seeding completed successfully!")
