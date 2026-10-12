"""wa_messages dedup table (WhatsApp webhook retry protection)

Revision ID: f3a9c21d7e45
Revises: c78ac7a1b4fd
Create Date: 2026-10-12 06:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a9c21d7e45'
down_revision = 'c78ac7a1b4fd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('wa_messages',
    sa.Column('message_id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=True),
    sa.Column('wa_type', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('message_id')
    )
    op.create_index(op.f('ix_wa_messages_user_id'), 'wa_messages', ['user_id'], unique=False)
    op.create_index(op.f('ix_wa_messages_created_at'), 'wa_messages', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_wa_messages_created_at'), table_name='wa_messages')
    op.drop_index(op.f('ix_wa_messages_user_id'), table_name='wa_messages')
    op.drop_table('wa_messages')
