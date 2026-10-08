"""Connection pool + tenant-bound transactions.

Every transaction is either bound to exactly one tenant (app.tenant_id) or is an explicit system transaction
(app.system). With neither set, row-level security returns nothing: missing tenant scope fails closed.
"""
import json
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=12, kwargs={"row_factory": dict_row}, open=False)


def open_pool():
    pool.open(wait=True, timeout=30)


@contextmanager
def tx(tenant_id: str | None = None, system: bool = False):
    if tenant_id is not None and system:
        raise ValueError("a transaction is tenant-bound or system, not both")
    with pool.connection() as conn:
        if tenant_id is not None:
            conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        elif system:
            conn.execute("select set_config('app.system', 'on', true)")
        yield conn


def J(obj) -> Jsonb:
    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=str))
