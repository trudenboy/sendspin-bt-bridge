import { visit } from 'unist-util-visit';

/**
 * Remark plugin that converts mermaid fenced code blocks into raw HTML
 * <pre class="mermaid"> elements BEFORE Expressive Code gets to them.
 *
 * Running as a remark plugin (not rehype) ensures it executes before
 * Starlight's Expressive Code remark plugin, which would otherwise wrap
 * the code in styled HTML that mermaid.js cannot parse.
 */
export default function remarkMermaid() {
  return (tree) => {
    visit(tree, 'code', (node, index, parent) => {
      if (node.lang !== 'mermaid' || !parent) return;

      // Escape HTML entities in the mermaid source to prevent
      // the browser from interpreting < > & as HTML.
      const escaped = node.value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      parent.children.splice(index, 1, {
        type: 'html',
        value: `<div class="mermaid-wrapper" style="overflow-x:auto;margin:1.5rem 0">\n<pre class="mermaid">${escaped}</pre>\n</div>`,
      });

      // Return index so visit doesn't skip the next node
      return index;
    });
  };
}
