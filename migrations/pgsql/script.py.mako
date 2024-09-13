"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)} # pragma: allowlist secret
down_revision = ${repr(down_revision)} # pragma: allowlist secret
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}
    sql1 = "grant read on all tables in schema public to andre_loose_readonly"
    op.execute(sql1)
    sql2 = "grant read on all tables in schema public to alice_prod_readonly"
    op.execute(sql2)


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
