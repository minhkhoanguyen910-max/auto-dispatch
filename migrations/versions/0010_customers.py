"""customers and their addresses

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
from geoalchemy2 import Geography
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('customers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone')
    )
    op.create_table('customer_addresses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=50), nullable=True),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography'), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_addresses_customer_id'), 'customer_addresses', ['customer_id'], unique=False)
    op.add_column('orders', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('customer_address_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_orders_customer_id'), 'orders', ['customer_id'], unique=False)
    op.create_foreign_key('orders_customer_id_fkey', 'orders', 'customers', ['customer_id'], ['id'])
    op.create_foreign_key('orders_customer_address_id_fkey', 'orders', 'customer_addresses', ['customer_address_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('orders_customer_address_id_fkey', 'orders', type_='foreignkey')
    op.drop_constraint('orders_customer_id_fkey', 'orders', type_='foreignkey')
    op.drop_index(op.f('ix_orders_customer_id'), table_name='orders')
    op.drop_column('orders', 'customer_address_id')
    op.drop_column('orders', 'customer_id')
    op.drop_index(op.f('ix_customer_addresses_customer_id'), table_name='customer_addresses')
    op.drop_table('customer_addresses')
    op.drop_table('customers')
