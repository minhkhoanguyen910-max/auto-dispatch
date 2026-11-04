"""drivers' messages to the bot (Plan 5): what they wrote, which order, and what it did; the web shows each as an alert

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-03 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'driver_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('driver_id', sa.Integer(), sa.ForeignKey('drivers.id'), nullable=False, index=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('meaning', sa.String(16), nullable=False),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('driver_messages')
