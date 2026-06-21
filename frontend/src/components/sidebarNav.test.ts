import { describe, expect, it } from 'vitest'

import { navItems } from '@/components/sidebarNav'

describe('sidebarNav', () => {
    it('does not expose the backtest module in navigation', () => {
        expect(navItems.some(item => item.path === '/backtests')).toBe(false)
        expect(navItems.some(item => item.label.includes('回测'))).toBe(false)
    })

    it('includes a dedicated tracking board entry in the sidebar', () => {
        const dashboardIndex = navItems.findIndex(item => item.path === '/')
        const trackingBoardIndex = navItems.findIndex(item => item.path === '/tracking-board')

        expect(trackingBoardIndex).toBeGreaterThan(0)
        expect(dashboardIndex).toBe(0)
        expect(navItems[trackingBoardIndex]).toMatchObject({
            path: '/tracking-board',
            label: '跟踪看板',
        })
        expect(trackingBoardIndex).toBeGreaterThan(dashboardIndex)
    })
})
