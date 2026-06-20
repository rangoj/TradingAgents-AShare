import { sanitizeReportMarkdown } from '@/utils/reportText'

export interface ReportExportSection {
    key: string
    title: string
}

export interface ReportExportSource {
    symbol?: string
    name?: string
    trade_date?: string
    [key: string]: unknown
}

function escapeHtml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;')
}

function renderInline(value: string): string {
    return escapeHtml(value)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function renderList(lines: string[], ordered: boolean): string {
    const tag = ordered ? 'ol' : 'ul'
    const items = lines.map(line => {
        const text = ordered ? line.replace(/^\d+\.\s+/, '') : line.replace(/^[-*]\s+/, '')
        return `<li>${renderInline(text)}</li>`
    }).join('')
    return `<${tag}>${items}</${tag}>`
}

function renderTable(lines: string[]): string {
    const rows = lines
        .filter(line => !/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line.trim()))
        .map(line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => renderInline(cell.trim())))
    if (rows.length === 0) return ''
    const [head, ...body] = rows
    return [
        '<table>',
        `<thead><tr>${head.map(cell => `<th>${cell}</th>`).join('')}</tr></thead>`,
        `<tbody>${body.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody>`,
        '</table>',
    ].join('')
}

function markdownToHtml(markdown: string): string {
    const lines = sanitizeReportMarkdown(markdown).replace(/\r\n/g, '\n').split('\n')
    const html: string[] = []

    for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i]
        const trimmed = line.trim()
        if (!trimmed) continue

        if (/^\|.+\|$/.test(trimmed) && i + 1 < lines.length && /^\|?\s*:?-{3,}/.test(lines[i + 1].trim())) {
            const tableLines = [trimmed, lines[i + 1].trim()]
            i += 2
            while (i < lines.length && /^\|.+\|$/.test(lines[i].trim())) {
                tableLines.push(lines[i].trim())
                i += 1
            }
            i -= 1
            html.push(renderTable(tableLines))
            continue
        }

        if (trimmed.startsWith('### ')) {
            html.push(`<h3>${renderInline(trimmed.slice(4))}</h3>`)
            continue
        }
        if (trimmed.startsWith('## ')) {
            html.push(`<h2>${renderInline(trimmed.slice(3))}</h2>`)
            continue
        }
        if (trimmed.startsWith('# ')) {
            html.push(`<h1>${renderInline(trimmed.slice(2))}</h1>`)
            continue
        }
        if (trimmed.startsWith('> ')) {
            html.push(`<blockquote>${renderInline(trimmed.slice(2))}</blockquote>`)
            continue
        }
        if (/^[-*]\s+/.test(trimmed)) {
            const listLines = [trimmed]
            while (i + 1 < lines.length && /^[-*]\s+/.test(lines[i + 1].trim())) {
                i += 1
                listLines.push(lines[i].trim())
            }
            html.push(renderList(listLines, false))
            continue
        }
        if (/^\d+\.\s+/.test(trimmed)) {
            const listLines = [trimmed]
            while (i + 1 < lines.length && /^\d+\.\s+/.test(lines[i + 1].trim())) {
                i += 1
                listLines.push(lines[i].trim())
            }
            html.push(renderList(listLines, true))
            continue
        }

        const paragraph = [trimmed]
        while (
            i + 1 < lines.length
            && lines[i + 1].trim()
            && !/^(#{1,3}\s|>\s|[-*]\s+|\d+\.\s+|\|.+\|$)/.test(lines[i + 1].trim())
        ) {
            i += 1
            paragraph.push(lines[i].trim())
        }
        html.push(`<p>${paragraph.map(renderInline).join('<br />')}</p>`)
    }

    return html.join('\n')
}

function buildPdfDocumentHtml(source: ReportExportSource, sections: ReportExportSection[], disclaimer: string): string {
    const displayName = source.name || source.symbol || '分析报告'
    const symbol = source.symbol ? String(source.symbol) : ''
    const tradeDate = source.trade_date ? String(source.trade_date) : ''
    const content = sections
        .filter(section => typeof source[section.key] === 'string' && String(source[section.key]).trim())
        .map(section => `
            <section>
                <h2>${escapeHtml(section.title)}</h2>
                ${markdownToHtml(String(source[section.key]))}
            </section>
        `)
        .join('')

    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>${escapeHtml(`analysis-${symbol || 'report'}-${tradeDate || 'latest'}`)}</title>
<style>
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
    margin: 0;
    color: #0f172a;
    background: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    line-height: 1.72;
}
header { margin-bottom: 24px; border-bottom: 2px solid #e2e8f0; padding-bottom: 16px; }
h1 { margin: 0 0 8px; font-size: 26px; line-height: 1.25; }
h2 { margin: 28px 0 12px; font-size: 18px; line-height: 1.35; color: #1d4ed8; page-break-after: avoid; }
h3 { margin: 20px 0 10px; font-size: 15px; line-height: 1.4; }
p { margin: 8px 0; font-size: 12px; }
.meta { color: #64748b; font-size: 12px; }
section { break-inside: auto; }
blockquote { margin: 10px 0; border-left: 3px solid #f59e0b; padding: 8px 12px; background: #fffbeb; color: #92400e; font-size: 12px; }
ul, ol { margin: 8px 0 8px 20px; padding: 0; font-size: 12px; }
li { margin: 4px 0; }
code { background: #f1f5f9; border-radius: 4px; padding: 1px 4px; font-family: "SFMono-Regular", Consolas, monospace; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 11px; page-break-inside: auto; }
th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f8fafc; font-weight: 700; }
.disclaimer { margin-top: 28px; border-top: 1px solid #e2e8f0; padding-top: 12px; }
@media print { .no-print { display: none; } }
</style>
</head>
<body>
<button class="no-print" onclick="window.print()" style="position:fixed;right:20px;top:20px;padding:8px 12px;border:1px solid #cbd5e1;border-radius:8px;background:#fff;cursor:pointer;">保存为 PDF</button>
<header>
    <h1>${escapeHtml(String(displayName))} 分析报告</h1>
    <div class="meta">${escapeHtml([symbol, tradeDate ? `分析日期：${tradeDate}` : ''].filter(Boolean).join(' · '))}</div>
</header>
${content}
<section class="disclaimer">${markdownToHtml(disclaimer)}</section>
<script>
window.addEventListener('load', () => {
    setTimeout(() => window.print(), 250);
});
</script>
</body>
</html>`
}

export function exportReportAsPdf(source: ReportExportSource, sections: ReportExportSection[], disclaimer: string): void {
    const printWindow = window.open('', '_blank', 'width=980,height=760')
    if (!printWindow) {
        window.alert('浏览器阻止了导出窗口，请允许弹窗后重试。')
        return
    }
    printWindow.document.open()
    printWindow.document.write(buildPdfDocumentHtml(source, sections, disclaimer))
    printWindow.document.close()
    printWindow.focus()
}
