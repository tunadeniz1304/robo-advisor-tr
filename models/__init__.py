"""ORM model package.

Importing this package registers every model on ``Base.metadata`` so that
Alembic autogenerate and the test bootstrap see the complete schema::

    from models import Customer, Portfolio, Transaction
"""

from models.advice import BLView, Goal, ModelPortfolio, RiskProfile
from models.advisor_run import AdvisorRun
from models.customer import Customer
from models.execution import AutopilotSetting, CashFlow, Fill, Order, RebalanceProposal
from models.governance import AuditLog, Nudge
from models.llm_usage import LLMUsage
from models.market import Instrument, MacroSeries, PriceHistory
from models.portfolio import Portfolio
from models.tax_lot import TaxLot
from models.transaction import Transaction
from models.user import User

__all__ = [
    "AdvisorRun",
    "AuditLog",
    "AutopilotSetting",
    "BLView",
    "CashFlow",
    "Customer",
    "Fill",
    "Goal",
    "Instrument",
    "LLMUsage",
    "MacroSeries",
    "ModelPortfolio",
    "Nudge",
    "Order",
    "Portfolio",
    "PriceHistory",
    "RebalanceProposal",
    "RiskProfile",
    "TaxLot",
    "Transaction",
    "User",
]
