"""Advisor router — exposes the multi-agent rebalancing workflow via REST.

POST /api/v1/advisor/rebalance/{portfolio_id}?customer_id=…

Runs the full LangGraph pipeline (market -> risk -> portfolio manager), applies
the rebalancing to the database (SQL UPDATE/INSERT) and returns target
weights, executed orders and the LLM narrative.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.config import Settings
from core.logging import get_logger
from llm.clients import LLMConfigurationError
from schemas.advisor import AdvisorResponse
from services.advisor_service import AdvisorService

logger = get_logger("otonom.router.advisor")
router = APIRouter(prefix="/advisor", tags=["advisor"])


def _advisor_service(request: Request, checkpoint_db: str | None = None) -> AdvisorService:
    """Build an AdvisorService from the app's settings (DI point)."""
    settings: Settings = request.app.state.settings
    return AdvisorService(settings=settings, checkpoint_db=checkpoint_db)


@router.post(
    "/rebalance/{portfolio_id}",
    response_model=AdvisorResponse,
    summary="Portföyü LangGraph ajanlarıyla yeniden dengele",
    description=(
        "Piyasa Ajanı gerçek Yahoo Finance verisini çeker, Risk Ajanı müşteri "
        "profilinden dinamik risk skoru üretir, Portföy Yöneticisi Markowitz "
        "MPT'yi gerçek LLM analiziyle birleştirip DB üzerinde rebalancing "
        "uygular (SQL UPDATE holdings/cash + INSERT transactions)."
    ),
)
async def rebalance_portfolio(
    request: Request,
    portfolio_id: int,
    customer_id: int = Query(..., gt=0, description="Müşteri kimliği"),
) -> AdvisorResponse:
    """Run the advisor workflow for a portfolio and persist the rebalance."""
    service = _advisor_service(request)

    if not service._settings.has_llm_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM sağlayıcısı yapılandırılmadı. .env içine OPENAI_API_KEY "
                "veya ANTHROPIC_API_KEY ekleyin (bkz. .env.example)."
            ),
        )

    try:
        result = await service.run_rebalance(portfolio_id=portfolio_id, customer_id=customer_id)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - beklenmedik hataları 500'e bağla
        logger.exception("advisor_rebalance_failed", portfolio_id=portfolio_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Advisor çalıştırılırken hata: {exc}",
        ) from exc

    if result.error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result.error)

    return AdvisorResponse(**result.to_dict())
