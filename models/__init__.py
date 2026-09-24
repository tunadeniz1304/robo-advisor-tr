"""ORM model package.

Importing this package registers every model on ``Base.metadata`` so that
``core.database.init_db`` creates the complete schema. The explicit re-exports
below give callers a single import point::

    from models import Customer, Portfolio, Transaction
"""

from models.advisor_run import AdvisorRun
from models.customer import Customer
from models.portfolio import Portfolio
from models.transaction import Transaction

__all__ = ["AdvisorRun", "Customer", "Portfolio", "Transaction"]
