"""Persistent full-range backtest service.

Runs the current multi-agent analysis for every CN trading day in a user
selected date range, then validates each AI decision against future prices.
Backtest results are intentionally stored in dedicated tables instead of the
normal report history.
"""
from __future__ import annotations

import io
import asyncio
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from api.database import BacktestJobDB, BacktestRecordDB, get_db_ctx
from api.services import report_service

DEFAULT_ACCURACY_THRESHOLD_PCT = 1.0
MAX_BACKTEST_TRADING_DAYS = int(os.getenv("BACKTEST_MAX_TRADING_DAYS", "260"))
FINISHED_RECORD_STATUSES = ("completed", "failed", "cancelled")
BACKTEST_INFLIGHT_WAIT_SECONDS = int(os.getenv("BACKTEST_INFLIGHT_WAIT_SECONDS", "1800"))
BACKTEST_INFLIGHT_POLL_SECONDS = float(os.getenv("BACKTEST_INFLIGHT_POLL_SECONDS", "5"))
BACKTEST_INFLIGHT_STALE_SECONDS = int(os.getenv("BACKTEST_INFLIGHT_STALE_SECONDS", "7200"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _round_float(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _datetime_key(value: Optional[datetime]) -> str:
    return value.isoformat() if value else ""


def _normalize_decision(decision: str | None) -> str:
    d = (decision or "").upper()
    if "BUY" in d:
        return "BUY"
    if "SELL" in d:
        return "SELL"
    return "HOLD"


def _analysts_key(selected_analysts: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip().lower() for item in (selected_analysts or []) if str(item).strip()}))


def _serialize_job(job: BacktestJobDB, records: Optional[List[BacktestRecordDB]] = None) -> dict:
    data = job.to_dict()
    if records is not None:
        ordered_records = sorted(records, key=lambda record: (record.trade_date or "", record.id or ""))
        data["records"] = [record.to_dict() for record in ordered_records]
    return data


def _find_reusable_record(
    db: Session,
    user_id: str,
    symbol: str,
    trade_date: str,
    selected_analysts: List[str],
    hold_days: int,
    exclude_record_id: Optional[str] = None,
) -> Optional[BacktestRecordDB]:
    candidates = (
        db.query(BacktestRecordDB, BacktestJobDB)
        .join(BacktestJobDB, BacktestRecordDB.job_id == BacktestJobDB.id)
        .filter(
            BacktestRecordDB.user_id == user_id,
            BacktestRecordDB.symbol == symbol,
            BacktestRecordDB.trade_date == trade_date,
            BacktestRecordDB.status == "completed",
            BacktestJobDB.user_id == user_id,
            BacktestJobDB.symbol == symbol,
        )
        .order_by(BacktestRecordDB.updated_at.desc())
        .all()
    )
    for record, job in candidates:
        if exclude_record_id and record.id == exclude_record_id:
            continue
        return record
    return None


def _find_inflight_record(
    db: Session,
    user_id: str,
    symbol: str,
    trade_date: str,
    selected_analysts: List[str],
    hold_days: int,
    exclude_record_id: Optional[str] = None,
    current_job_created_at: Optional[datetime] = None,
    current_job_id: Optional[str] = None,
) -> Optional[BacktestRecordDB]:
    stale_before = _utcnow() - timedelta(seconds=BACKTEST_INFLIGHT_STALE_SECONDS)
    candidates = (
        db.query(BacktestRecordDB, BacktestJobDB)
        .join(BacktestJobDB, BacktestRecordDB.job_id == BacktestJobDB.id)
        .filter(
            BacktestRecordDB.user_id == user_id,
            BacktestRecordDB.symbol == symbol,
            BacktestRecordDB.trade_date == trade_date,
            BacktestRecordDB.status.in_(("pending", "running")),
            BacktestRecordDB.updated_at >= stale_before,
            BacktestJobDB.user_id == user_id,
            BacktestJobDB.symbol == symbol,
            BacktestJobDB.status.in_(("pending", "running")),
        )
        .order_by(BacktestRecordDB.updated_at.asc())
        .all()
    )
    for record, job in candidates:
        if exclude_record_id and record.id == exclude_record_id:
            continue
        if record.status == "running":
            return record
        if current_job_created_at is None:
            return record
        candidate_key = (_datetime_key(job.created_at), job.id or "", record.id or "")
        current_key = (_datetime_key(current_job_created_at), current_job_id or "", exclude_record_id or "")
        if candidate_key < current_key:
            return record
    return None


def _copy_reused_fields(target: BacktestRecordDB, source: BacktestRecordDB) -> None:
    target.status = "completed"
    target.error = None
    target.decision = source.decision
    target.direction = source.direction
    target.confidence = source.confidence
    target.target_price = source.target_price
    target.stop_loss_price = source.stop_loss_price
    target.entry_price = source.entry_price
    target.exit_price = source.exit_price
    target.future_return_pct = source.future_return_pct
    target.strategy_return_pct = source.strategy_return_pct
    target.max_high = source.max_high
    target.min_low = source.min_low
    target.target_hit = source.target_hit
    target.stop_loss_hit = source.stop_loss_hit
    target.is_correct = source.is_correct
    target.decision_summary = source.decision_summary
    target.result_snapshot = source.result_snapshot
    target.reused_from_job_id = source.job_id
    target.reused_from_record_id = source.id
    target.updated_at = _utcnow()


def _try_reuse_record(
    db: Session,
    record: BacktestRecordDB,
    selected_analysts: List[str],
    hold_days: int,
) -> bool:
    source = _find_reusable_record(
        db,
        user_id=record.user_id,
        symbol=record.symbol,
        trade_date=record.trade_date,
        selected_analysts=selected_analysts,
        hold_days=hold_days,
        exclude_record_id=record.id,
    )
    if not source:
        return False
    _copy_reused_fields(record, source)
    return True


def _build_reused_record(
    source: BacktestRecordDB,
    *,
    record_id: str,
    job_id: str,
    user_id: str,
    symbol: str,
    trade_date: str,
    now: datetime,
) -> BacktestRecordDB:
    return BacktestRecordDB(
        id=record_id,
        job_id=job_id,
        user_id=user_id,
        symbol=symbol,
        trade_date=trade_date,
        status="completed",
        error=None,
        decision=source.decision,
        direction=source.direction,
        confidence=source.confidence,
        target_price=source.target_price,
        stop_loss_price=source.stop_loss_price,
        entry_price=source.entry_price,
        exit_price=source.exit_price,
        future_return_pct=source.future_return_pct,
        strategy_return_pct=source.strategy_return_pct,
        max_high=source.max_high,
        min_low=source.min_low,
        target_hit=source.target_hit,
        stop_loss_hit=source.stop_loss_hit,
        is_correct=source.is_correct,
        decision_summary=source.decision_summary,
        result_snapshot=source.result_snapshot,
        reused_from_job_id=source.job_id,
        reused_from_record_id=source.id,
        created_at=now,
        updated_at=now,
    )


def _record_merge_rank(record: BacktestRecordDB) -> tuple[int, int, str]:
    status_rank = {
        "completed": 5,
        "running": 4,
        "pending": 3,
        "failed": 2,
        "cancelled": 1,
    }.get(record.status or "", 0)
    original_rank = 1 if record.status == "completed" and not record.reused_from_record_id else 0
    return (status_rank, original_rank, _datetime_key(record.updated_at))


def _refresh_job_date_bounds(db: Session, job: BacktestJobDB) -> None:
    records = (
        db.query(BacktestRecordDB)
        .filter(BacktestRecordDB.job_id == job.id)
        .order_by(BacktestRecordDB.trade_date.asc())
        .all()
    )
    if records:
        job.start_date = records[0].trade_date
        job.end_date = records[-1].trade_date
        job.total_dates = len(records)
    else:
        job.total_dates = 0
        job.completed_dates = 0
    job.updated_at = _utcnow()


def _merge_symbol_jobs(db: Session, user_id: str, symbol: str) -> Optional[BacktestJobDB]:
    jobs = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.user_id == user_id, BacktestJobDB.symbol == symbol)
        .order_by(BacktestJobDB.created_at.desc(), BacktestJobDB.id.desc())
        .all()
    )
    if not jobs:
        return None
    canonical = jobs[0]
    duplicate_jobs = jobs[1:]
    if not duplicate_jobs:
        _refresh_job_date_bounds(db, canonical)
        _apply_job_stats(db, canonical.id)
        return canonical

    record_groups: Dict[str, List[BacktestRecordDB]] = {}
    for job in jobs:
        for record in db.query(BacktestRecordDB).filter(BacktestRecordDB.job_id == job.id).all():
            record_groups.setdefault(record.trade_date, []).append(record)

    for trade_date, records in record_groups.items():
        winner = sorted(records, key=_record_merge_rank, reverse=True)[0]
        losers = [record for record in records if record.id != winner.id]
        for loser in losers:
            db.delete(loser)
        db.flush()
        winner.job_id = canonical.id
        winner.user_id = user_id
        winner.symbol = symbol
        winner.updated_at = _utcnow()

    for job in duplicate_jobs:
        db.delete(job)
    db.flush()
    _refresh_job_date_bounds(db, canonical)
    _apply_job_stats(db, canonical.id)
    return canonical


def merge_all_symbol_jobs(db: Session, user_id: str) -> None:
    symbols = [row[0] for row in db.query(BacktestJobDB.symbol).filter(BacktestJobDB.user_id == user_id).distinct().all()]
    for symbol in symbols:
        _merge_symbol_jobs(db, user_id, symbol)
    db.commit()


def list_jobs(db: Session, user_id: str, skip: int = 0, limit: int = 50) -> List[dict]:
    merge_all_symbol_jobs(db, user_id)
    jobs = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.user_id == user_id)
        .order_by(BacktestJobDB.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_serialize_job(job) for job in jobs]


def count_jobs(db: Session, user_id: str) -> int:
    merge_all_symbol_jobs(db, user_id)
    return db.query(BacktestJobDB).filter(BacktestJobDB.user_id == user_id).count()


def get_job(db: Session, user_id: str, job_id: str, include_records: bool = True) -> Optional[dict]:
    job = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.id == job_id, BacktestJobDB.user_id == user_id)
        .first()
    )
    if not job:
        return None
    job = _merge_symbol_jobs(db, user_id, job.symbol) or job
    db.commit()
    records = None
    if include_records:
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == job_id, BacktestRecordDB.user_id == user_id)
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )
    return _serialize_job(job, records)


def delete_job(db: Session, user_id: str, job_id: str) -> bool:
    job = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.id == job_id, BacktestJobDB.user_id == user_id)
        .first()
    )
    if not job:
        return False
    db.query(BacktestRecordDB).filter(
        BacktestRecordDB.job_id == job_id,
        BacktestRecordDB.user_id == user_id,
    ).delete(synchronize_session=False)
    db.delete(job)
    db.commit()
    return True


def cancel_job(db: Session, user_id: str, job_id: str) -> Optional[dict]:
    job = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.id == job_id, BacktestJobDB.user_id == user_id)
        .first()
    )
    if not job:
        return None
    if job.status in ("completed", "failed", "cancelled"):
        return _serialize_job(job)

    now = _utcnow()
    job.status = "cancelled"
    job.error = "用户已停止回测"
    job.finished_at = now
    job.updated_at = now
    (
        db.query(BacktestRecordDB)
        .filter(
            BacktestRecordDB.job_id == job_id,
            BacktestRecordDB.user_id == user_id,
            BacktestRecordDB.status.in_(("pending", "running")),
        )
        .update(
            {
                "status": "cancelled",
                "error": "用户已停止回测",
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )
    _apply_job_stats(db, job_id, status="cancelled", error="用户已停止回测")
    db.commit()
    return _serialize_job(job)


def estimate_trading_dates(start_date: str, end_date: str) -> List[str]:
    return _get_trading_dates(start_date, end_date)


def _get_trading_dates(start_date: str, end_date: str) -> List[str]:
    """Return every CN trading day in [start_date, end_date]."""
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")

    dates: list[str] = []
    cur = start
    while cur <= end:
        value = cur.strftime("%Y-%m-%d")
        if is_cn_trading_day(value):
            dates.append(value)
        cur = cur.fromordinal(cur.toordinal() + 1)
    return dates


def _parse_stock_csv(csv_data: str) -> pd.DataFrame:
    if not csv_data:
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(csv_data), comment="#")
    if df.empty:
        return pd.DataFrame()

    col_map = {
        "日期": "Date",
        "date": "Date",
        "开盘": "Open",
        "open": "Open",
        "最高": "High",
        "high": "High",
        "最低": "Low",
        "low": "Low",
        "收盘": "Close",
        "close": "Close",
        "成交量": "Volume",
        "volume": "Volume",
    }
    df = df.rename(columns={c: col_map.get(c, c) for c in df.columns})
    required = ["Date", "High", "Low", "Close"]
    if any(c not in df.columns for c in required):
        return pd.DataFrame()
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    for col in ["High", "Low", "Close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=required).sort_values("Date").reset_index(drop=True)
    return out


def _get_price_validation(symbol: str, trade_date: str, hold_days: int) -> Dict[str, Any]:
    """Return entry/exit prices and future high/low window for one sample."""
    from tradingagents.dataflows.interface import route_to_vendor

    start_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=max(hold_days * 4, hold_days + 14))
    csv_data = route_to_vendor(
        "get_stock_data",
        symbol,
        trade_date,
        end_dt.strftime("%Y-%m-%d"),
    )
    df = _parse_stock_csv(csv_data)
    if df.empty:
        return {}

    trade_ts = pd.to_datetime(trade_date)
    df = df[df["Date"] >= trade_ts].reset_index(drop=True)
    if df.empty:
        return {}

    entry = df.iloc[0]
    future = df[df["Date"] > entry["Date"]].reset_index(drop=True)
    if future.empty:
        return {"entry_price": _to_float(entry["Close"])}

    exit_index = min(max(hold_days, 1), len(future)) - 1
    exit_row = future.iloc[exit_index]
    validation_window = future.iloc[: exit_index + 1]
    entry_price = _to_float(entry["Close"])
    exit_price = _to_float(exit_row["Close"])
    if not entry_price or not exit_price:
        future_return_pct = None
    else:
        future_return_pct = (exit_price - entry_price) / entry_price * 100

    return {
        "entry_price": _round_float(entry_price),
        "exit_price": _round_float(exit_price),
        "future_return_pct": _round_float(future_return_pct),
        "max_high": _round_float(_to_float(validation_window["High"].max())),
        "min_low": _round_float(_to_float(validation_window["Low"].min())),
    }


def _build_result_snapshot(final_state: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "macro_report",
        "smart_money_report",
        "volume_price_report",
        "game_theory_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
        "analyst_traces",
    ]
    return {key: final_state.get(key) for key in keys if final_state.get(key) is not None}


def _run_single_analysis(
    symbol: str,
    trade_date: str,
    selected_analysts: List[str],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one current multi-agent analysis and return normalized fields."""
    return asyncio.run(_run_single_analysis_async(symbol, trade_date, selected_analysts, config))


async def _run_single_analysis_async(
    symbol: str,
    trade_date: str,
    selected_analysts: List[str],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one current multi-agent analysis through LangGraph's async API."""
    from tradingagents.dataflows.config import set_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    set_config(config)
    graph = TradingAgentsGraph(
        selected_analysts=selected_analysts,
        debug=False,
        config=config,
    )
    await asyncio.to_thread(graph.data_collector.collect, symbol, trade_date, horizons=["short"])
    graph_args = graph.propagator.get_graph_args()
    graph_args.setdefault("config", {})
    graph_args["config"]["configurable"] = {
        "thread_id": f"backtest_{symbol}_{trade_date}_{uuid4().hex[:8]}"
    }
    state = graph.propagator.create_initial_state(
        symbol,
        trade_date,
        user_context={},
        selected_analysts=selected_analysts,
        request_source="backtest",
        user_intent={
            "raw_query": "",
            "ticker": symbol,
            "horizons": ["short"],
            "focus_areas": [],
            "specific_questions": [],
            "user_context": {},
        },
        horizon="short",
    )
    try:
        final_state = await graph.graph.ainvoke(state, **graph_args)
    finally:
        graph.data_collector.evict(symbol, trade_date)

    decision = graph.process_signal(final_state.get("final_trade_decision", ""))
    snapshot = _build_result_snapshot(final_state)
    resolved = report_service.resolve_report_fields(result_data=snapshot)
    return {
        "decision": _normalize_decision(decision),
        "direction": resolved.get("direction"),
        "confidence": resolved.get("confidence"),
        "target_price": resolved.get("target_price"),
        "stop_loss_price": resolved.get("stop_loss_price"),
        "decision_summary": (snapshot.get("final_trade_decision") or "")[:500],
        "result_snapshot": snapshot,
    }


def _score_record(record: Dict[str, Any], threshold_pct: float = DEFAULT_ACCURACY_THRESHOLD_PCT) -> Dict[str, Any]:
    decision = _normalize_decision(record.get("decision"))
    future_return = record.get("future_return_pct")
    strategy_return = None
    is_correct = None

    if future_return is not None:
        if decision == "BUY":
            strategy_return = future_return
            is_correct = future_return >= threshold_pct
        elif decision == "SELL":
            strategy_return = -future_return
            is_correct = future_return <= -threshold_pct
        else:
            strategy_return = 0.0
            is_correct = abs(future_return) < threshold_pct

    target_price = record.get("target_price")
    stop_loss_price = record.get("stop_loss_price")
    max_high = record.get("max_high")
    min_low = record.get("min_low")

    target_hit = None
    if target_price is not None:
        if decision == "SELL":
            target_hit = min_low is not None and min_low <= target_price
        else:
            target_hit = max_high is not None and max_high >= target_price

    stop_loss_hit = None
    if stop_loss_price is not None:
        if decision == "SELL":
            stop_loss_hit = max_high is not None and max_high >= stop_loss_price
        else:
            stop_loss_hit = min_low is not None and min_low <= stop_loss_price

    return {
        "strategy_return_pct": _round_float(strategy_return),
        "is_correct": is_correct,
        "target_hit": target_hit,
        "stop_loss_hit": stop_loss_hit,
    }


def _compute_stats(records: Iterable[BacktestRecordDB]) -> Dict[str, Any]:
    records_list = list(records)
    completed = [r for r in records_list if r.status == "completed"]
    scored = [r for r in completed if r.is_correct is not None]
    actionable = [r for r in scored if r.decision in ("BUY", "SELL")]
    strategy_returns = [r.strategy_return_pct for r in completed if r.strategy_return_pct is not None]
    future_returns = [r.future_return_pct for r in completed if r.future_return_pct is not None]
    target_records = [r for r in completed if r.target_hit is not None]
    stop_records = [r for r in completed if r.stop_loss_hit is not None]
    failed_count = len([r for r in records_list if r.status == "failed"])
    cancelled_count = len([r for r in records_list if r.status == "cancelled"])
    reused_count = len([r for r in records_list if r.reused_from_record_id])

    def pct(part: int, total: int) -> Optional[float]:
        return None if total == 0 else round(part / total * 100, 1)

    correct_count = len([r for r in scored if r.is_correct])
    target_hit_count = len([r for r in target_records if r.target_hit])
    stop_loss_hit_count = len([r for r in stop_records if r.stop_loss_hit])

    return {
        "total_records": len(records_list),
        "completed_records": len(completed),
        "failed_records": failed_count,
        "cancelled_records": cancelled_count,
        "reused_records": reused_count,
        "scored_records": len(scored),
        "accuracy_pct": pct(correct_count, len(scored)),
        "actionable_accuracy_pct": pct(len([r for r in actionable if r.is_correct]), len(actionable)),
        "buy_count": len([r for r in completed if r.decision == "BUY"]),
        "sell_count": len([r for r in completed if r.decision == "SELL"]),
        "hold_count": len([r for r in completed if r.decision == "HOLD"]),
        "avg_future_return_pct": _round_float(sum(future_returns) / len(future_returns)) if future_returns else None,
        "avg_strategy_return_pct": _round_float(sum(strategy_returns) / len(strategy_returns)) if strategy_returns else None,
        "cumulative_strategy_return_pct": _round_float(sum(strategy_returns)) if strategy_returns else None,
        "best_strategy_return_pct": _round_float(max(strategy_returns)) if strategy_returns else None,
        "worst_strategy_return_pct": _round_float(min(strategy_returns)) if strategy_returns else None,
        "target_hit_rate_pct": pct(target_hit_count, len(target_records)),
        "stop_loss_hit_rate_pct": pct(stop_loss_hit_count, len(stop_records)),
    }


def _apply_job_stats(db: Session, job_id: str, status: Optional[str] = None, error: Optional[str] = None) -> bool:
    job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()
    if not job:
        return False
    records = (
        db.query(BacktestRecordDB)
        .filter(BacktestRecordDB.job_id == job_id)
        .order_by(BacktestRecordDB.trade_date.asc())
        .all()
    )
    job.completed_dates = len([r for r in records if r.status in FINISHED_RECORD_STATUSES])
    job.stats = _compute_stats(records)
    if status:
        job.status = status
    if error:
        job.error = error
    if status in ("completed", "failed", "cancelled"):
        job.finished_at = _utcnow()
    return True


def _update_job_stats(job_id: str, status: Optional[str] = None, error: Optional[str] = None) -> None:
    with get_db_ctx() as db:
        if _apply_job_stats(db, job_id, status=status, error=error):
            db.commit()


def _is_job_cancelled(job_id: str) -> bool:
    with get_db_ctx() as db:
        job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()
        return bool(job and job.status == "cancelled")


def _mark_remaining_records_cancelled(job_id: str) -> None:
    now = _utcnow()
    with get_db_ctx() as db:
        db.query(BacktestRecordDB).filter(
            BacktestRecordDB.job_id == job_id,
            BacktestRecordDB.status.in_(("pending", "running")),
        ).update(
            {
                "status": "cancelled",
                "error": "用户已停止回测",
                "updated_at": now,
            },
            synchronize_session=False,
        )
        db.commit()
    _update_job_stats(job_id, status="cancelled", error="用户已停止回测")


def _prepare_record_for_analysis(
    record_id: str,
    selected_analysts: List[str],
    hold_days: int,
    *,
    wait_for_inflight: bool = True,
) -> tuple[str, Optional[str]]:
    deadline = time.monotonic() + BACKTEST_INFLIGHT_WAIT_SECONDS
    while True:
        with get_db_ctx() as db:
            record = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == record_id).first()
            if not record:
                return "missing", None
            job = db.query(BacktestJobDB).filter(BacktestJobDB.id == record.job_id).first()
            if not job:
                return "missing", None
            if record.status == "cancelled":
                return "cancelled", record.job_id
            if record.status == "completed":
                return "completed", record.job_id
            if record.status == "running":
                stale_before = _utcnow() - timedelta(seconds=BACKTEST_INFLIGHT_STALE_SECONDS)
                if record.updated_at and record.updated_at >= stale_before:
                    return "busy", record.job_id
            if _try_reuse_record(db, record, selected_analysts, hold_days):
                job_id = record.job_id
                db.commit()
                _update_job_stats(job_id)
                return "reused", job_id

            inflight = _find_inflight_record(
                db,
                user_id=record.user_id,
                symbol=record.symbol,
                trade_date=record.trade_date,
                selected_analysts=selected_analysts,
                hold_days=hold_days,
                exclude_record_id=record.id,
                current_job_created_at=job.created_at,
                current_job_id=job.id,
            )
            if inflight and wait_for_inflight and time.monotonic() < deadline:
                record.status = "pending"
                record.error = f"等待复用 {inflight.trade_date} 的进行中样本"
                record.updated_at = _utcnow()
                db.commit()
                _update_job_stats(record.job_id)
            else:
                record.status = "running"
                record.error = None
                record.reused_from_job_id = None
                record.reused_from_record_id = None
                record.updated_at = _utcnow()
                job_id = record.job_id
                trade_date = record.trade_date
                db.commit()
                _update_job_stats(job_id)
                return "run", trade_date

        time.sleep(BACKTEST_INFLIGHT_POLL_SECONDS)


def _complete_record_with_payload(record_id: str, payload: Dict[str, Any], score: Dict[str, Any]) -> Optional[str]:
    with get_db_ctx() as db:
        record = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == record_id).first()
        if not record:
            return None
        if record.status == "cancelled":
            _mark_remaining_records_cancelled(record.job_id)
            return None
        record.status = "completed"
        record.error = None
        record.decision = payload.get("decision")
        record.direction = payload.get("direction")
        record.confidence = payload.get("confidence")
        record.target_price = payload.get("target_price")
        record.stop_loss_price = payload.get("stop_loss_price")
        record.entry_price = payload.get("entry_price")
        record.exit_price = payload.get("exit_price")
        record.future_return_pct = payload.get("future_return_pct")
        record.max_high = payload.get("max_high")
        record.min_low = payload.get("min_low")
        record.strategy_return_pct = score.get("strategy_return_pct")
        record.is_correct = score.get("is_correct")
        record.target_hit = score.get("target_hit")
        record.stop_loss_hit = score.get("stop_loss_hit")
        record.decision_summary = payload.get("decision_summary")
        record.result_snapshot = payload.get("result_snapshot")
        record.reused_from_job_id = None
        record.reused_from_record_id = None
        record.updated_at = _utcnow()
        job_id = record.job_id
        db.commit()
    return job_id


def _fail_record(record_id: str, exc: Exception) -> Optional[str]:
    with get_db_ctx() as db:
        record = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == record_id).first()
        if not record:
            return None
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        record.updated_at = _utcnow()
        job_id = record.job_id
        db.commit()
    return job_id


def _clear_record_result(record: BacktestRecordDB) -> None:
    record.status = "pending"
    record.error = None
    record.decision = None
    record.direction = None
    record.confidence = None
    record.target_price = None
    record.stop_loss_price = None
    record.entry_price = None
    record.exit_price = None
    record.future_return_pct = None
    record.strategy_return_pct = None
    record.max_high = None
    record.min_low = None
    record.target_hit = None
    record.stop_loss_hit = None
    record.is_correct = None
    record.decision_summary = None
    record.result_snapshot = None
    record.reused_from_job_id = None
    record.reused_from_record_id = None
    record.updated_at = _utcnow()


def _finalize_job_if_idle(job_id: str) -> None:
    with get_db_ctx() as db:
        records = db.query(BacktestRecordDB).filter(BacktestRecordDB.job_id == job_id).all()
        active_count = len([r for r in records if r.status in ("pending", "running")])
        completed_count = len([r for r in records if r.status == "completed"])
        failed_count = len([r for r in records if r.status == "failed"])
    if active_count > 0:
        _update_job_stats(job_id, status="running")
    elif completed_count == 0 and failed_count > 0:
        _update_job_stats(job_id, status="failed", error="所有回测样本均失败，请查看明细错误")
    else:
        _update_job_stats(job_id, status="completed")


def _run_single_record_retry(job_id: str, record_id: str, config: Dict[str, Any]) -> None:
    try:
        with get_db_ctx() as db:
            job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()
            record = db.query(BacktestRecordDB).filter(BacktestRecordDB.id == record_id).first()
            if not job or not record:
                return
            symbol = job.symbol
            selected_analysts = list(job.selected_analysts or [])
            hold_days = int(job.hold_days or 5)

        action, value = _prepare_record_for_analysis(record_id, selected_analysts, hold_days)
        if action in ("missing", "cancelled", "completed", "reused"):
            _finalize_job_if_idle(job_id)
            return

        trade_date = value
        analysis = _run_single_analysis(symbol, trade_date, selected_analysts, config)
        validation = _get_price_validation(symbol, trade_date, hold_days)
        payload = {**analysis, **validation}
        score = _score_record(payload)
        _complete_record_with_payload(record_id, payload, score)
    except Exception as exc:
        _fail_record(record_id, exc)
    finally:
        _finalize_job_if_idle(job_id)


def retry_record(
    db: Session,
    user_id: str,
    job_id: str,
    record_id: str,
    config: Dict[str, Any],
) -> Optional[dict]:
    job = (
        db.query(BacktestJobDB)
        .filter(BacktestJobDB.id == job_id, BacktestJobDB.user_id == user_id)
        .first()
    )
    record = (
        db.query(BacktestRecordDB)
        .filter(
            BacktestRecordDB.id == record_id,
            BacktestRecordDB.job_id == job_id,
            BacktestRecordDB.user_id == user_id,
        )
        .first()
    )
    if not job or not record:
        return None
    if record.status in ("pending", "running"):
        return get_job(db, user_id, job_id)

    _clear_record_result(record)
    job.status = "running"
    job.error = None
    job.finished_at = None
    job.updated_at = _utcnow()
    _apply_job_stats(db, job_id)
    db.commit()

    thread = threading.Thread(target=_run_single_record_retry, args=(job_id, record_id, config), daemon=True)
    thread.start()
    return get_job(db, user_id, job_id)


def _run_backtest(job_id: str, config: Dict[str, Any]) -> None:
    with get_db_ctx() as db:
        job = db.query(BacktestJobDB).filter(BacktestJobDB.id == job_id).first()
        if not job:
            return
        job.status = "running"
        job.started_at = _utcnow()
        db.commit()
        symbol = job.symbol
        selected_analysts = list(job.selected_analysts or [])
        hold_days = int(job.hold_days or 5)
        records = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == job_id)
            .filter(BacktestRecordDB.status == "pending")
            .order_by(BacktestRecordDB.trade_date.asc())
            .all()
        )
        record_ids = [record.id for record in records]

    try:
        for record_id in record_ids:
            if _is_job_cancelled(job_id):
                _mark_remaining_records_cancelled(job_id)
                return

            action, value = _prepare_record_for_analysis(record_id, selected_analysts, hold_days)
            if action in ("missing", "cancelled", "completed", "reused"):
                continue
            trade_date = value

            try:
                analysis = _run_single_analysis(symbol, trade_date, selected_analysts, config)
                validation = _get_price_validation(symbol, trade_date, hold_days)
                payload = {**analysis, **validation}
                score = _score_record(payload)

                if _is_job_cancelled(job_id):
                    _mark_remaining_records_cancelled(job_id)
                    return

                _complete_record_with_payload(record_id, payload, score)
            except Exception as exc:
                if _is_job_cancelled(job_id):
                    _mark_remaining_records_cancelled(job_id)
                    return
                _fail_record(record_id, exc)

            _update_job_stats(job_id)

        if _is_job_cancelled(job_id):
            _mark_remaining_records_cancelled(job_id)
            return

        _finalize_job_if_idle(job_id)
    except Exception as exc:
        _update_job_stats(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:300]}\n{traceback.format_exc()[:1200]}",
        )


def submit(
    db: Session,
    user_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
    selected_analysts: List[str],
    hold_days: int,
    config: Dict[str, Any],
) -> str:
    trading_dates = _get_trading_dates(start_date, end_date)
    if not trading_dates:
        raise ValueError("所选日期范围内没有 A 股交易日")
    if len(trading_dates) > MAX_BACKTEST_TRADING_DAYS:
        raise ValueError(
            f"本次回测包含 {len(trading_dates)} 个交易日，超过上限 {MAX_BACKTEST_TRADING_DAYS}。"
            "请缩短日期范围或调整 BACKTEST_MAX_TRADING_DAYS"
        )
    if hold_days < 1:
        raise ValueError("验证周期至少为 1 个交易日")

    now = _utcnow()
    normalized_symbol = symbol.strip().upper()
    job = _merge_symbol_jobs(db, user_id, normalized_symbol)
    if job:
        job.hold_days = hold_days
        job.selected_analysts = selected_analysts
        job.error = None
        job.finished_at = None
        job.updated_at = now
    else:
        job = BacktestJobDB(
            id=uuid4().hex,
            user_id=user_id,
            symbol=normalized_symbol,
            start_date=start_date,
            end_date=end_date,
            hold_days=hold_days,
            selected_analysts=selected_analysts,
            status="pending",
            total_dates=0,
            completed_dates=0,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.flush()

    existing_dates = {
        row[0]
        for row in db.query(BacktestRecordDB.trade_date)
        .filter(BacktestRecordDB.job_id == job.id)
        .all()
    }
    pending_count = 0
    for trade_date in trading_dates:
        if trade_date in existing_dates:
            continue
        reused_record = _find_reusable_record(
            db,
            user_id=user_id,
            symbol=normalized_symbol,
            trade_date=trade_date,
            selected_analysts=selected_analysts,
            hold_days=hold_days,
        )
        if reused_record:
            record = _build_reused_record(
                reused_record,
                record_id=uuid4().hex,
                job_id=job.id,
                user_id=user_id,
                symbol=normalized_symbol,
                trade_date=trade_date,
                now=now,
            )
        else:
            pending_count += 1
            record = BacktestRecordDB(
                id=uuid4().hex,
                job_id=job.id,
                user_id=user_id,
                symbol=normalized_symbol,
                trade_date=trade_date,
                status="pending",
                created_at=now,
                updated_at=now,
            )
        db.add(record)
        existing_dates.add(trade_date)

    if pending_count > 0:
        job.status = "running" if job.status == "running" else "pending"
    db.flush()
    _refresh_job_date_bounds(db, job)
    _apply_job_stats(db, job.id)
    db.commit()

    if pending_count == 0:
        active_count = (
            db.query(BacktestRecordDB)
            .filter(BacktestRecordDB.job_id == job.id, BacktestRecordDB.status.in_(("pending", "running")))
            .count()
        )
        if active_count == 0 and job.status not in ("failed", "cancelled"):
            _apply_job_stats(db, job.id, status="completed")
            db.commit()
        return job.id

    thread = threading.Thread(target=_run_backtest, args=(job.id, config), daemon=True)
    thread.start()
    return job.id
