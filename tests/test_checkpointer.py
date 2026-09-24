"""Durable checkpointer: a run paused at the approval interrupt survives a
"restart" (new graph instance on the same SQLite file) and resumes."""

from __future__ import annotations

from pathlib import Path

from core.database import adopt_engine, create_engine_from_url, init_db
from services.advisor_service import AdvisorService
from tests.conftest import make_settings


async def _seed(tmp_path: Path) -> tuple[int, int]:
    from core.database import session_factory
    from models import Customer, Portfolio

    async with session_factory() as s:
        c = Customer(
            full_name="Test Kişi",
            email="cp@example.com",
            investment_horizon_years=10,
            declared_risk_tolerance=4,
        )
        s.add(c)
        await s.flush()
        p = Portfolio(customer_id=c.id, name="P", cash=100_000, holdings={})
        s.add(p)
        await s.commit()
        return c.id, p.id


async def test_interrupt_persists_and_resumes_after_restart(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}")
    adopt_engine(engine)
    await init_db(engine)
    checkpoint = str(tmp_path / "checkpoints.sqlite")
    settings = make_settings(tmp_path)
    try:
        cid, pid = await _seed(tmp_path)
        first = AdvisorService(settings, checkpoint_db=checkpoint)
        result = await first.start(pid, cid)
        assert result.error is None and result.status == "ONAY_BEKLIYOR"
        thread = result.run_id
        graph = await first.graph()
        assert type(graph.checkpointer).__name__ == "AsyncSqliteSaver"
        assert await graph.is_waiting(thread)
        await first.aclose()  # "yeniden başlatma"

        second = AdvisorService(settings, checkpoint_db=checkpoint)
        assert await (await second.graph()).is_waiting(thread)
        executed = await second.approve(result.proposal_id, user_id=None, role="admin")
        assert executed["status"] == "YURUTULDU"
        assert not await (await second.graph()).is_waiting(thread)
        await second.aclose()
        assert Path(checkpoint).exists()  # noqa: ASYNC240
    finally:
        await engine.dispose()
