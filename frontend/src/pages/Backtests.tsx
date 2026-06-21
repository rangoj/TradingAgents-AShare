import { useEffect, useMemo, useState } from 'react'
import { Activity, BarChart3, CalendarDays, CheckCircle2, Clock3, Loader2, Play, RefreshCcw, Square, Trash2, XCircle } from 'lucide-react'

import { api } from '@/services/api'
import type { BacktestEstimateResponse, BacktestJob, BacktestRecord, WatchlistItem } from '@/types'

const ANALYST_OPTIONS = [
    { key: 'market', label: '技术' },
    { key: 'social', label: '情绪' },
    { key: 'news', label: '新闻' },
    { key: 'fundamentals', label: '基本面' },
    { key: 'macro', label: '宏观' },
    { key: 'smart_money', label: '主力资金' },
    { key: 'volume_price', label: '量价' },
]

const DEFAULT_ANALYSTS = ANALYST_OPTIONS.map(item => item.key)
const DEFAULT_BACKTEST_TRADING_DAYS = 10

function formatDateInput(date: Date): string {
    return date.toISOString().slice(0, 10)
}

function defaultStartDate(): string {
    const date = new Date()
    date.setDate(date.getDate() - 21)
    return formatDateInput(date)
}

function formatPct(value?: number | null): string {
    if (value == null || Number.isNaN(value)) return '-'
    return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatNum(value?: number | null): string {
    if (value == null || Number.isNaN(value)) return '-'
    return value.toFixed(2)
}

function statusLabel(status: BacktestJob['status'] | BacktestRecord['status']): string {
    const map = { pending: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已停止' }
    return map[status] ?? status
}

function statusClass(status: BacktestJob['status'] | BacktestRecord['status']): string {
    if (status === 'completed') return 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/30'
    if (status === 'failed') return 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:border-rose-500/30'
    if (status === 'running') return 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-300 dark:border-blue-500/30'
    if (status === 'cancelled') return 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/30'
    return 'bg-slate-50 text-slate-600 border-slate-200 dark:bg-slate-700/40 dark:text-slate-300 dark:border-slate-600'
}

function decisionClass(decision?: string | null): string {
    if (decision === 'BUY') return 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300'
    if (decision === 'SELL') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
    return 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300'
}

export default function Backtests() {
    const [jobs, setJobs] = useState<BacktestJob[]>([])
    const [selectedJob, setSelectedJob] = useState<BacktestJob | null>(null)
    const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
    const [symbol, setSymbol] = useState('')
    const [startDate, setStartDate] = useState(defaultStartDate)
    const [endDate, setEndDate] = useState(() => formatDateInput(new Date()))
    const [holdDays, setHoldDays] = useState(5)
    const [selectedAnalysts, setSelectedAnalysts] = useState<string[]>(DEFAULT_ANALYSTS)
    const [estimate, setEstimate] = useState<BacktestEstimateResponse | null>(null)
    const [loading, setLoading] = useState(false)
    const [submitting, setSubmitting] = useState(false)
    const [stoppingJobId, setStoppingJobId] = useState<string | null>(null)
    const [retryingRecordId, setRetryingRecordId] = useState<string | null>(null)
    const [watchlistLoading, setWatchlistLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [detailError, setDetailError] = useState<string | null>(null)

    const activeJob = selectedJob && ['pending', 'running'].includes(selectedJob.status)
    const progress = selectedJob && selectedJob.total_dates > 0
        ? Math.round((selectedJob.completed_dates / selectedJob.total_dates) * 100)
        : 0

    const sortedRecords = useMemo(() => {
        return [...(selectedJob?.records ?? [])].sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    }, [selectedJob])

    const groupedJobs = useMemo(() => {
        const groups = new Map<string, { symbol: string; name: string; jobs: BacktestJob[] }>()
        for (const job of jobs) {
            const key = job.symbol
            const group = groups.get(key) ?? { symbol: job.symbol, name: job.name || job.symbol, jobs: [] }
            group.jobs.push(job)
            groups.set(key, group)
        }
        return Array.from(groups.values()).map(group => ({
            ...group,
            jobs: group.jobs.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')),
        }))
    }, [jobs])

    async function loadJobs(selectJobId?: string) {
        setLoading(true)
        setError(null)
        try {
            const response = await api.getBacktests()
            setJobs(response.jobs)
            const next = selectJobId
                ? response.jobs.find(job => job.id === selectJobId || job.job_id === selectJobId)
                : selectedJob
                    ? response.jobs.find(job => job.id === selectedJob.id)
                    : response.jobs[0]
            if (next) {
                await loadJobDetail(next.id)
            } else {
                setSelectedJob(null)
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : '加载回测历史失败')
        } finally {
            setLoading(false)
        }
    }

    async function loadJobDetail(jobId: string) {
        setDetailError(null)
        try {
            const detail = await api.getBacktest(jobId)
            setSelectedJob(detail)
        } catch (err) {
            setDetailError(err instanceof Error ? err.message : '加载回测详情失败')
        }
    }

    async function loadWatchlist() {
        setWatchlistLoading(true)
        try {
            const response = await api.getWatchlist()
            setWatchlist(response.items)
            setSymbol(current => {
                if (current && response.items.some(item => item.symbol === current)) return current
                return response.items[0]?.symbol ?? ''
            })
        } catch (err) {
            setError(err instanceof Error ? err.message : '加载自选股失败')
        } finally {
            setWatchlistLoading(false)
        }
    }

    async function refreshEstimate() {
        if (!startDate || !endDate) return
        try {
            const response = await api.estimateBacktest(startDate, endDate)
            setEstimate(response)
        } catch {
            setEstimate(null)
        }
    }

    async function initializeDefaultDateRange() {
        const end = formatDateInput(new Date())
        for (const lookbackDays of [21, 35, 60]) {
            const start = new Date()
            start.setDate(start.getDate() - lookbackDays)
            try {
                const response = await api.estimateBacktest(formatDateInput(start), end)
                if (response.dates.length >= DEFAULT_BACKTEST_TRADING_DAYS) {
                    setStartDate(response.dates[response.dates.length - DEFAULT_BACKTEST_TRADING_DAYS])
                    setEndDate(end)
                    setEstimate({
                        ...response,
                        dates: response.dates.slice(-DEFAULT_BACKTEST_TRADING_DAYS),
                        total_dates: DEFAULT_BACKTEST_TRADING_DAYS,
                    })
                    return
                }
            } catch {
                // Keep the local fallback dates if the estimate endpoint is unavailable.
            }
        }
    }

    useEffect(() => {
        void loadJobs()
        void loadWatchlist()
        void initializeDefaultDateRange()
    }, [])

    useEffect(() => {
        const handle = window.setTimeout(() => {
            void refreshEstimate()
        }, 300)
        return () => window.clearTimeout(handle)
    }, [startDate, endDate])

    useEffect(() => {
        if (!activeJob || !selectedJob) return
        const handle = window.setInterval(() => {
            void loadJobDetail(selectedJob.id)
            void loadJobs(selectedJob.id)
        }, 5000)
        return () => window.clearInterval(handle)
    }, [activeJob, selectedJob?.id])

    async function submitBacktest() {
        setSubmitting(true)
        setError(null)
        try {
            const response = await api.createBacktest({
                symbol: symbol.trim().toUpperCase(),
                start_date: startDate,
                end_date: endDate,
                hold_days: holdDays,
                selected_analysts: selectedAnalysts,
            })
            await loadJobs(response.job_id)
        } catch (err) {
            setError(err instanceof Error ? err.message : '提交回测失败')
        } finally {
            setSubmitting(false)
        }
    }

    async function deleteBacktest(job: BacktestJob) {
        if (!window.confirm(`确定删除 ${job.symbol} ${job.start_date} 至 ${job.end_date} 的回测历史吗？`)) return
        try {
            await api.deleteBacktest(job.id)
            if (selectedJob?.id === job.id) setSelectedJob(null)
            await loadJobs()
        } catch (err) {
            setError(err instanceof Error ? err.message : '删除回测失败')
        }
    }

    async function stopBacktest(job: BacktestJob) {
        setStoppingJobId(job.id)
        setError(null)
        try {
            const cancelled = await api.cancelBacktest(job.id)
            setSelectedJob(current => (current?.id === cancelled.id ? { ...current, ...cancelled } : current))
            await loadJobs(job.id)
        } catch (err) {
            setError(err instanceof Error ? err.message : '停止回测失败')
        } finally {
            setStoppingJobId(null)
        }
    }

    async function retryRecord(record: BacktestRecord) {
        if (!selectedJob) return
        setRetryingRecordId(record.id)
        setError(null)
        try {
            const updated = await api.retryBacktestRecord(selectedJob.id, record.id)
            setSelectedJob(updated)
            await loadJobs(updated.id)
        } catch (err) {
            setError(err instanceof Error ? err.message : '启动子任务失败')
        } finally {
            setRetryingRecordId(null)
        }
    }

    function toggleAnalyst(key: string) {
        setSelectedAnalysts(current => {
            if (current.includes(key)) {
                return current.length <= 1 ? current : current.filter(item => item !== key)
            }
            return [...current, key]
        })
    }

    const stats = selectedJob?.stats

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                <div>
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-500 dark:text-slate-400">
                        <BarChart3 className="h-4 w-4" />
                        独立回测历史
                    </div>
                    <h1 className="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">完整交易日回测</h1>
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                        区间内每个 A 股交易日都会运行一次当前多 Agent 分析，结果只进入回测历史。
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => loadJobs(selectedJob?.id)}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                >
                    <RefreshCcw className="h-4 w-4" />
                    刷新
                </button>
            </div>

            {error && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                    {error}
                </div>
            )}

            <section className="card">
                <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <label className="space-y-1.5">
                            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">自选股票</span>
                            <select
                                value={symbol}
                                onChange={event => setSymbol(event.target.value)}
                                disabled={watchlistLoading || watchlist.length === 0}
                                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:disabled:bg-slate-800"
                            >
                                {watchlist.length === 0 ? (
                                    <option value="">{watchlistLoading ? '加载自选股中...' : '暂无自选股'}</option>
                                ) : watchlist.map(item => (
                                    <option key={item.id} value={item.symbol}>
                                        {item.name || item.symbol}（{item.symbol}）
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="space-y-1.5">
                            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">开始日期</span>
                            <input
                                type="date"
                                value={startDate}
                                onChange={event => setStartDate(event.target.value)}
                                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                            />
                        </label>
                        <label className="space-y-1.5">
                            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">结束日期</span>
                            <input
                                type="date"
                                value={endDate}
                                onChange={event => setEndDate(event.target.value)}
                                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                            />
                        </label>
                        <label className="space-y-1.5">
                            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">验证周期</span>
                            <select
                                value={holdDays}
                                onChange={event => setHoldDays(Number(event.target.value))}
                                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-emerald-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                            >
                                {[3, 5, 10, 20].map(value => (
                                    <option key={value} value={value}>{value} 个交易日</option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 dark:border-slate-700 dark:bg-slate-800/40">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <p className="text-xs font-semibold uppercase text-slate-400">预计任务量</p>
                                <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">
                                    {estimate ? `${estimate.total_dates} 个交易日` : '-'}
                                </p>
                            </div>
                            <CalendarDays className="h-8 w-8 text-emerald-500" />
                        </div>
                        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                            当前后端上限 {estimate?.max_dates ?? '-'} 个交易日；完整回测会逐日运行多 Agent，耗时和模型调用量较高。
                        </p>
                    </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                    {ANALYST_OPTIONS.map(item => (
                        <button
                            key={item.key}
                            type="button"
                            onClick={() => toggleAnalyst(item.key)}
                            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${selectedAnalysts.includes(item.key)
                                ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-300'
                                : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400 dark:hover:bg-slate-800'
                                }`}
                        >
                            {item.label}
                        </button>
                    ))}
                </div>

                <div className="mt-4 flex justify-end">
                    <div className="flex flex-col items-end gap-2">
                        {watchlist.length === 0 && !watchlistLoading && (
                            <p className="text-xs text-amber-600 dark:text-amber-300">请先在“自选 & 定时”中添加股票，再启动回测。</p>
                        )}
                        <button
                            type="button"
                            onClick={submitBacktest}
                            disabled={submitting || watchlistLoading || watchlist.length === 0 || !symbol.trim() || !startDate || !endDate || selectedAnalysts.length === 0}
                            className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                            启动完整回测
                        </button>
                    </div>
                </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
                <section className="card">
                    <div className="mb-4 flex items-center justify-between">
                        <h2 className="font-semibold text-slate-900 dark:text-slate-100">回测任务</h2>
                        {loading && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
                    </div>
                    <div className="space-y-3">
                        {jobs.length === 0 && !loading ? (
                            <div className="rounded-lg border border-dashed border-slate-200 p-5 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
                                暂无回测历史
                            </div>
                        ) : groupedJobs.map(group => (
                            <div key={group.symbol} className="space-y-2">
                                <div className="flex items-center justify-between px-1 text-xs text-slate-500 dark:text-slate-400">
                                    <span className="font-semibold text-slate-700 dark:text-slate-200">{group.name}</span>
                                    <span>{group.jobs.length} 次回测</span>
                                </div>
                                {group.jobs.map(job => (
                                    <button
                                        key={job.id}
                                        type="button"
                                        onClick={() => loadJobDetail(job.id)}
                                        className={`w-full rounded-xl border p-3 text-left transition ${selectedJob?.id === job.id
                                            ? 'border-emerald-300 bg-emerald-50/80 dark:border-emerald-500/40 dark:bg-emerald-500/10'
                                            : 'border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800'
                                            }`}
                                    >
                                        <div className="flex items-start justify-between gap-3">
                                            <div>
                                                <p className="font-semibold text-slate-900 dark:text-slate-100">{job.start_date} 至 {job.end_date}</p>
                                                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">验证 {job.hold_days} 个交易日</p>
                                            </div>
                                            <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusClass(job.status)}`}>
                                                {statusLabel(job.status)}
                                            </span>
                                        </div>
                                        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                                            <div
                                                className="h-full rounded-full bg-emerald-500"
                                                style={{ width: `${job.total_dates ? Math.round((job.completed_dates / job.total_dates) * 100) : 0}%` }}
                                            />
                                        </div>
                                        <p className="mt-2 text-xs text-slate-400">{job.completed_dates}/{job.total_dates} 个交易日</p>
                                    </button>
                                ))}
                            </div>
                        ))}
                    </div>
                </section>

                <section className="card min-w-0">
                    {!selectedJob ? (
                        <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-slate-200 text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
                            选择一个回测任务查看结果
                        </div>
                    ) : (
                        <div className="space-y-5">
                            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                                <div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{selectedJob.name || selectedJob.symbol}</h2>
                                        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusClass(selectedJob.status)}`}>
                                            {statusLabel(selectedJob.status)}
                                        </span>
                                    </div>
                                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                                        {selectedJob.start_date} 至 {selectedJob.end_date}，验证 {selectedJob.hold_days} 个交易日
                                    </p>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {['pending', 'running'].includes(selectedJob.status) && (
                                        <button
                                            type="button"
                                            onClick={() => stopBacktest(selectedJob)}
                                            disabled={stoppingJobId === selectedJob.id}
                                            className="inline-flex items-center justify-center gap-2 rounded-lg border border-amber-200 px-3 py-2 text-sm font-medium text-amber-700 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-500/30 dark:text-amber-300 dark:hover:bg-amber-500/10"
                                        >
                                            {stoppingJobId === selectedJob.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                                            停止
                                        </button>
                                    )}
                                    <button
                                        type="button"
                                        onClick={() => deleteBacktest(selectedJob)}
                                        className="inline-flex items-center justify-center gap-2 rounded-lg border border-rose-200 px-3 py-2 text-sm font-medium text-rose-600 hover:bg-rose-50 dark:border-rose-500/30 dark:text-rose-300 dark:hover:bg-rose-500/10"
                                    >
                                        <Trash2 className="h-4 w-4" />
                                        删除
                                    </button>
                                </div>
                            </div>

                            {detailError && (
                                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                                    {detailError}
                                </div>
                            )}

                            <div>
                                <div className="mb-2 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                                    <span>执行进度</span>
                                    <span>{selectedJob.completed_dates}/{selectedJob.total_dates} ({progress}%)</span>
                                </div>
                                <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                                    <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${progress}%` }} />
                                </div>
                                {(stats?.reused_records ?? 0) > 0 && (
                                    <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                                        已复用历史样本 {stats?.reused_records} 个，未重复运行多 Agent。
                                    </p>
                                )}
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                                <MetricCard icon={<CheckCircle2 className="h-4 w-4" />} label="方向命中率" value={stats?.accuracy_pct == null ? '-' : `${stats.accuracy_pct}%`} />
                                <MetricCard icon={<Activity className="h-4 w-4" />} label="策略累计收益" value={formatPct(stats?.cumulative_strategy_return_pct)} />
                                <MetricCard icon={<BarChart3 className="h-4 w-4" />} label="平均策略收益" value={formatPct(stats?.avg_strategy_return_pct)} />
                                <MetricCard icon={<Clock3 className="h-4 w-4" />} label="已评分样本" value={`${stats?.scored_records ?? 0}/${stats?.total_records ?? selectedJob.total_dates}`} />
                            </div>

                            <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
                                <div className="overflow-x-auto">
                                    <table className="min-w-full divide-y divide-slate-200 text-sm dark:divide-slate-700">
                                        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                                            <tr>
                                                <th className="px-3 py-2 text-left font-medium">日期</th>
                                                <th className="px-3 py-2 text-left font-medium">结论</th>
                                                <th className="px-3 py-2 text-right font-medium">未来收益</th>
                                                <th className="px-3 py-2 text-right font-medium">策略收益</th>
                                                <th className="px-3 py-2 text-right font-medium">入场/退出</th>
                                                <th className="px-3 py-2 text-center font-medium">命中</th>
                                                <th className="px-3 py-2 text-right font-medium">操作</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                            {sortedRecords.map(record => (
                                                <tr key={record.id} className="bg-white dark:bg-slate-900">
                                                    <td className="whitespace-nowrap px-3 py-2 text-slate-700 dark:text-slate-200">{record.trade_date}</td>
                                                    <td className="px-3 py-2">
                                                        {record.status === 'completed' ? (
                                                            <>
                                                                <span className={`rounded-full px-2 py-1 text-xs font-semibold ${decisionClass(record.decision)}`}>
                                                                    {record.decision || 'HOLD'}
                                                                </span>
                                                                {record.reused_from_record_id ? (
                                                                    <div className="mt-1 text-[11px] text-slate-400">复用历史</div>
                                                                ) : null}
                                                            </>
                                                        ) : (
                                                            <>
                                                                <span className={`rounded-full border px-2 py-1 text-xs ${statusClass(record.status)}`}>
                                                                    {statusLabel(record.status)}
                                                                </span>
                                                                {record.status === 'failed' && record.error ? (
                                                                    <div className="mt-1 max-w-[280px] truncate text-[11px] text-rose-500" title={record.error}>
                                                                        {record.error}
                                                                    </div>
                                                                ) : null}
                                                            </>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-200">{formatPct(record.future_return_pct)}</td>
                                                    <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-200">{formatPct(record.strategy_return_pct)}</td>
                                                    <td className="whitespace-nowrap px-3 py-2 text-right text-slate-500 dark:text-slate-400">
                                                        {formatNum(record.entry_price)} / {formatNum(record.exit_price)}
                                                    </td>
                                                    <td className="px-3 py-2 text-center">
                                                        {record.is_correct == null ? (
                                                            <span className="text-slate-300">-</span>
                                                        ) : record.is_correct ? (
                                                            <CheckCircle2 className="mx-auto h-4 w-4 text-emerald-500" />
                                                        ) : (
                                                            <XCircle className="mx-auto h-4 w-4 text-rose-500" />
                                                        )}
                                                    </td>
                                                    <td className="whitespace-nowrap px-3 py-2 text-right">
                                                        {['failed', 'cancelled'].includes(record.status) ? (
                                                            <button
                                                                type="button"
                                                                onClick={() => retryRecord(record)}
                                                                disabled={retryingRecordId === record.id}
                                                                className="inline-flex items-center justify-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                                                            >
                                                                {retryingRecordId === record.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                                                                启动
                                                            </button>
                                                        ) : (
                                                            <span className="text-slate-300">-</span>
                                                        )}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    )}
                </section>
            </div>
        </div>
    )
}

function MetricCard({ icon, label, value }: { icon: JSX.Element; label: string; value: string }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-500 dark:text-slate-400">
                <span className="text-emerald-500">{icon}</span>
                {label}
            </div>
            <p className="mt-2 text-xl font-bold text-slate-900 dark:text-slate-100">{value}</p>
        </div>
    )
}
