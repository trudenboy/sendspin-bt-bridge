import axe from 'axe-core'

// axe-core in happy-dom: there is no layout or painting, so rules that need
// pixels cannot run (colour contrast, target size, visibility by geometry).
// Accessible names, roles, ARIA validity, labels and landmarks-in-context are
// all checked. Contrast is covered by the design tokens, not by these tests.
const NEEDS_LAYOUT = ['color-contrast', 'color-contrast-enhanced', 'target-size', 'scrollable-region-focusable']
// A component is mounted on its own, not inside a page: page-level rules do not apply.
const PAGE_LEVEL = ['region', 'landmark-one-main', 'page-has-heading-one', 'document-title', 'html-has-lang', 'bypass']

export interface Finding {
  rule: string
  impact: string
  target: string
  summary: string
}

/** Serious and critical axe violations in what the test mounted into document.body, or `[]`. */
export async function seriousViolations(root: HTMLElement = document.body): Promise<Finding[]> {
  const result = await axe.run(root, {
    rules: Object.fromEntries([...NEEDS_LAYOUT, ...PAGE_LEVEL].map((id) => [id, { enabled: false }])),
    resultTypes: ['violations'],
  })
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .flatMap((v) =>
      v.nodes.map((n) => ({
        rule: v.id,
        impact: String(v.impact),
        target: n.target.join(' '),
        summary: n.html.slice(0, 120),
      })),
    )
}
