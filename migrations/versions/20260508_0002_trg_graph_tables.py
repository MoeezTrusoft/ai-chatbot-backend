"""trg graph tables

Revision ID: 20260508_0002
Revises: 20260507_0001
Create Date: 2026-05-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0002"
down_revision: str | None = "20260507_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_graph_nodes_thread", "graph_nodes", ["thread_id"])
    op.create_index("ix_graph_nodes_type", "graph_nodes", ["node_type"])
    op.create_index("ix_graph_nodes_turn", "graph_nodes", ["turn_sequence"])

    op.create_table(
        "graph_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("compliance_score", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_graph_edges_thread", "graph_edges", ["thread_id"])
    op.create_index("ix_graph_edges_source", "graph_edges", ["source_node_id"])
    op.create_index("ix_graph_edges_target", "graph_edges", ["target_node_id"])
    op.create_index("ix_graph_edges_relation", "graph_edges", ["relation_type"])


def downgrade() -> None:
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
