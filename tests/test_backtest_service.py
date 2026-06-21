from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.database import BacktestJobDB, BacktestRecordDB, ReportDB, UserDB, get_db_ctx, init_db
from api.services import auth_service, backtest_service


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


class _InlineThread:
    def __init__(self, target, args=(), kwargs=None, **_extra):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def _create_token() -> str:
    init_db()
    now = datetime.now(timezone.utc)
    email = auth_service.normalize_email(f"backtest-{uuid4().hex[:8]}@test.com")
    with get_db_ctx() as db:
        user = UserDB(
            id=str(uuid4()),
            email=email,
            is_active=True,
            created_at=now,
            updated_at=now,
            last_login_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return auth_service.create_access_token(user)


def test_submit_persists_backtest_history_without_report_rows(monkeypatch):
    init_db()
    user_id = f"user-{uuid4().hex}"
    monkeypatch.setattr(
        "tradingagents.dataflows.trade_calendar.is_cn_trading_day",
        lambda date_str: date_str != "2026-03-03",
    )
    monkeypatch.setattr(backtest_service.threading, "Thread", _NoopThread)

    with get_db_ctx() as db:
        job_id = backtest_service.submit(
            db=db,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-04",
            selected_analysts=["market", "news"],
            hold_days=5,
            config={},
        )

        job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == job_id)
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )
        report_count = db.query(ReportDB).filter(ReportDB.user_id == user_id).count()

    assert job is not None
    assert job.total_dates == 2
    assert [record.trade_date for record in records] == ["2026-03-02", "2026-03-04"]
    assert report_count == 0


def test_cancel_job_marks_unfinished_records_cancelled(monkeypatch):
    init_db()
    user_id = f"user-{uuid4().hex}"
    monkeypatch.setattr("tradingagents.dataflows.trade_calendar.is_cn_trading_day", lambda date_str: True)
    monkeypatch.setattr(backtest_service.threading, "Thread", _NoopThread)

    with get_db_ctx() as db:
        job_id = backtest_service.submit(
            db=db,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-04",
            selected_analysts=["market", "news"],
            hold_days=5,
            config={},
        )

    with get_db_ctx() as db:
        cancelled = backtest_service.cancel_job(db, user_id, job_id)
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == job_id)
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "用户已停止回测"
    assert cancelled["completed_dates"] == 3
    assert cancelled["stats"]["cancelled_records"] == 3
    assert [record.status for record in records] == ["cancelled", "cancelled", "cancelled"]


def test_submit_appends_missing_dates_to_existing_symbol_job(monkeypatch):
    init_db()
    user_id = f"user-{uuid4().hex}"
    monkeypatch.setattr("tradingagents.dataflows.trade_calendar.is_cn_trading_day", lambda date_str: True)
    monkeypatch.setattr(backtest_service.threading, "Thread", _NoopThread)

    with get_db_ctx() as db:
        job_id = backtest_service.submit(
            db=db,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-02",
            selected_analysts=["news", "market"],
            hold_days=5,
            config={},
        )

    with get_db_ctx() as db:
        source_record = db.query(BacktestRecordDB).filter(BacktestRecordDB.job_id == job_id).first()
        source_record.status = "completed"
        source_record.decision = "BUY"
        source_record.entry_price = 100
        source_record.exit_price = 103
        source_record.future_return_pct = 3
        source_record.strategy_return_pct = 3
        source_record.is_correct = True
        db.commit()

    with get_db_ctx() as db:
        appended_job_id = backtest_service.submit(
            db=db,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-03",
            selected_analysts=["market", "news"],
            hold_days=5,
            config={},
        )
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == appended_job_id)
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )
        appended_job = db.query(BacktestJobDB).filter(BacktestJobDB.id == appended_job_id).first()

    assert appended_job_id == job_id
    assert [record.status for record in records] == ["completed", "pending"]
    assert [record.trade_date for record in records] == ["2026-03-02", "2026-03-03"]
    assert records[0].decision == "BUY"
    assert appended_job.total_dates == 2
    assert appended_job.completed_dates == 1


def test_merge_all_symbol_jobs_keeps_one_job_and_deduplicates_dates():
    init_db()
    user_id = f"user-{uuid4().hex}"
    now = datetime.now(timezone.utc)

    with get_db_ctx() as db:
        older_job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-03",
            hold_days=5,
            selected_analysts=["market"],
            status="completed",
            total_dates=2,
            completed_dates=2,
            created_at=now,
            updated_at=now,
        )
        newer_job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-03",
            end_date="2026-03-04",
            hold_days=5,
            selected_analysts=["news"],
            status="completed",
            total_dates=2,
            completed_dates=1,
            created_at=now + timedelta(microseconds=1),
            updated_at=now,
        )
        db.add_all([older_job, newer_job])
        db.add_all(
            [
                BacktestRecordDB(
                    id=uuid4().hex,
                    job_id=older_job.id,
                    user_id=user_id,
                    symbol="600519.SH",
                    trade_date="2026-03-02",
                    status="completed",
                    decision="BUY",
                    created_at=now,
                    updated_at=now,
                ),
                BacktestRecordDB(
                    id=uuid4().hex,
                    job_id=older_job.id,
                    user_id=user_id,
                    symbol="600519.SH",
                    trade_date="2026-03-03",
                    status="failed",
                    created_at=now,
                    updated_at=now,
                ),
                BacktestRecordDB(
                    id=uuid4().hex,
                    job_id=newer_job.id,
                    user_id=user_id,
                    symbol="600519.SH",
                    trade_date="2026-03-03",
                    status="completed",
                    decision="SELL",
                    created_at=now,
                    updated_at=now,
                ),
                BacktestRecordDB(
                    id=uuid4().hex,
                    job_id=newer_job.id,
                    user_id=user_id,
                    symbol="600519.SH",
                    trade_date="2026-03-04",
                    status="pending",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.commit()

        backtest_service.merge_all_symbol_jobs(db, user_id)
        jobs = db.query(BacktestJobDB).filter(BacktestJobDB.user_id == user_id, BacktestJobDB.symbol == "600519.SH").all()
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.user_id == user_id, BacktestRecordDB.symbol == "600519.SH")
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )

    assert len(jobs) == 1
    assert [record.trade_date for record in records] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert [record.status for record in records] == ["completed", "completed", "pending"]


def test_retry_failed_record_reuses_matching_completed_sample(monkeypatch):
    init_db()
    user_id = f"user-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        backtest_service,
        "_run_single_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retry should reuse instead of analyzing")),
    )

    with get_db_ctx() as db:
        source_job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-02",
            hold_days=5,
            selected_analysts=["market", "news"],
            status="completed",
            total_dates=1,
            completed_dates=1,
            created_at=now,
            updated_at=now,
        )
        target_job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-02",
            hold_days=5,
            selected_analysts=["market", "news"],
            status="failed",
            total_dates=1,
            completed_dates=1,
            created_at=now,
            updated_at=now,
        )
        source_record = BacktestRecordDB(
            id=uuid4().hex,
            job_id=source_job.id,
            user_id=user_id,
            symbol="600519.SH",
            trade_date="2026-03-02",
            status="completed",
            decision="BUY",
            future_return_pct=3,
            strategy_return_pct=3,
            is_correct=True,
            created_at=now,
            updated_at=now,
        )
        target_record = BacktestRecordDB(
            id=uuid4().hex,
            job_id=target_job.id,
            user_id=user_id,
            symbol="600519.SH",
            trade_date="2026-03-02",
            status="failed",
            error="old failure",
            created_at=now,
            updated_at=now,
        )
        db.add_all([source_job, target_job, source_record, target_record])
        db.commit()
        target_job_id = target_job.id
        target_record_id = target_record.id
        source_record_id = source_record.id

    backtest_service._run_single_record_retry(target_job_id, target_record_id, {})

    with get_db_ctx() as db:
        refreshed = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == target_record_id).first()
        refreshed_job = db.query(BacktestJobDB).filter(BacktestJobDB.id == target_job_id).first()

    assert refreshed.status == "completed"
    assert refreshed.error is None
    assert refreshed.reused_from_record_id == source_record_id
    assert refreshed_job.status == "completed"


def test_retry_cancelled_record_starts_single_record(monkeypatch):
    init_db()
    user_id = f"user-{uuid4().hex}"
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(backtest_service.threading, "Thread", _InlineThread)
    monkeypatch.setattr(
        backtest_service,
        "_run_single_analysis",
        lambda *args, **kwargs: {
            "decision": "BUY",
            "direction": "看多",
            "confidence": 70,
            "target_price": None,
            "stop_loss_price": None,
            "decision_summary": "test",
            "result_snapshot": {},
        },
    )
    monkeypatch.setattr(
        backtest_service,
        "_get_price_validation",
        lambda *args, **kwargs: {
            "entry_price": 100,
            "exit_price": 102,
            "future_return_pct": 2,
            "max_high": 103,
            "min_low": 99,
        },
    )

    with get_db_ctx() as db:
        job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol="600519.SH",
            start_date="2026-03-02",
            end_date="2026-03-02",
            hold_days=5,
            selected_analysts=["market", "news"],
            status="cancelled",
            total_dates=1,
            completed_dates=1,
            created_at=now,
            updated_at=now,
        )
        record = BacktestRecordDB(
            id=uuid4().hex,
            job_id=job.id,
            user_id=user_id,
            symbol="600519.SH",
            trade_date="2026-03-02",
            status="cancelled",
            error="用户已停止回测",
            created_at=now,
            updated_at=now,
        )
        db.add_all([job, record])
        db.commit()
        job_id = job.id
        record_id = record.id

    with get_db_ctx() as db:
        updated = backtest_service.retry_record(db, user_id, job_id, record_id, {})

    with get_db_ctx() as db:
        refreshed = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == record_id).first()
        refreshed_job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()

    assert updated is not None
    assert refreshed.status == "completed"
    assert refreshed.error is None
    assert refreshed.decision == "BUY"
    assert refreshed.strategy_return_pct == 2
    assert refreshed_job.status == "completed"


def test_get_job_returns_records_in_trade_date_order():
    init_db()
    user_id = f"user-{uuid4().hex}"
    job_id = uuid4().hex
    now = datetime.now(timezone.utc)

    with get_db_ctx() as db:
        db.add(
            BacktestJobDB(
                id=job_id,
                user_id=user_id,
                symbol="600519.SH",
                start_date="2026-03-02",
                end_date="2026-03-04",
                hold_days=5,
                selected_analysts=["market", "news"],
                status="completed",
                total_dates=3,
                completed_dates=3,
                created_at=now,
                updated_at=now,
            )
        )
        for trade_date in ["2026-03-04", "2026-03-02", "2026-03-03"]:
            db.add(
                BacktestRecordDB(
                    id=uuid4().hex,
                    job_id=job_id,
                    user_id=user_id,
                    symbol="600519.SH",
                    trade_date=trade_date,
                    status="completed",
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()

        job = backtest_service.get_job(db, user_id, job_id)

    assert [record["trade_date"] for record in job["records"]] == [
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
    ]


def test_score_record_separates_direction_accuracy_from_strategy_return():
    buy = backtest_service._score_record({"decision": "BUY", "future_return_pct": 2.5})
    sell = backtest_service._score_record({"decision": "SELL", "future_return_pct": -3.0})
    hold = backtest_service._score_record({"decision": "HOLD", "future_return_pct": 0.4})

    assert buy["is_correct"] is True
    assert buy["strategy_return_pct"] == 2.5
    assert sell["is_correct"] is True
    assert sell["strategy_return_pct"] == 3.0
    assert hold["is_correct"] is True
    assert hold["strategy_return_pct"] == 0.0


def test_backtest_estimate_route_uses_static_path_before_job_id(monkeypatch):
    from api.main import app

    token = _create_token()
    monkeypatch.setattr(
        "api.main._bt.estimate_trading_dates",
        lambda start_date, end_date: ["2026-03-02", "2026-03-03"],
    )
    monkeypatch.setattr("api.main._bt.MAX_BACKTEST_TRADING_DAYS", 260)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/v1/backtest/estimate?start_date=2026-03-01&end_date=2026-03-05",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["total_dates"] == 2
