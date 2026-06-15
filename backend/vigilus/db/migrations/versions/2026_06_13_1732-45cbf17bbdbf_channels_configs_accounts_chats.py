"""channels: configs, accounts, chats

Revision ID: 45cbf17bbdbf
Revises: f7e8b4029c06
Create Date: 2026-06-13 17:32:19.822857+00:00

Adds the three channel tables (ChannelConfig, ChannelAccount, ChannelChat)
and a nullable ``origin`` column on ``sessions`` so chats created via
Telegram/Discord/the scheduler can be badged in the UI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45cbf17bbdbf'
down_revision: Union[str, None] = 'f7e8b4029c06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'channel_configs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('bot_token_enc', sa.Text(), nullable=False),
        sa.Column('bot_username', sa.String(length=255), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('respond_in_groups', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('default_operator_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['default_operator_id'], ['operators.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', name='uq_channel_config_platform'),
    )
    op.create_index('ix_channel_configs_platform', 'channel_configs', ['platform'])

    op.create_table(
        'channel_accounts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('external_user_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('allowed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', 'external_user_id', name='uq_channel_account'),
    )
    op.create_index('ix_channel_accounts_platform', 'channel_accounts', ['platform'])

    op.create_table(
        'channel_chats',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('external_chat_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', 'external_chat_id', name='uq_channel_chat'),
    )
    op.create_index('ix_channel_chats_platform', 'channel_chats', ['platform'])

    op.add_column('sessions', sa.Column('origin', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'origin')
    op.drop_index('ix_channel_chats_platform', table_name='channel_chats')
    op.drop_table('channel_chats')
    op.drop_index('ix_channel_accounts_platform', table_name='channel_accounts')
    op.drop_table('channel_accounts')
    op.drop_index('ix_channel_configs_platform', table_name='channel_configs')
    op.drop_table('channel_configs')
