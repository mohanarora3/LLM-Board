// A small, safe markdown renderer: everything is HTML-escaped first, then a known
// subset (headings, lists, tables, code, bold/italic, links, citations) is formatted.

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const CITE_RUN = /((?:\s?\[\d{1,2}\](?!\())+)/g;

function inline(raw, opts) {
  const codes = [];
  let text = escapeHtml(raw).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  text = text
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  if (opts.cite) {
    text = text.replace(CITE_RUN, (run) => {
      const nums = [...run.matchAll(/\[(\d{1,2})\]/g)].map((m) => Number(m[1]));
      const html = opts.cite(nums);
      return html ? (run.startsWith(" ") ? " " : "") + html : run;
    });
  }
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

function isTableSep(line) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}

function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function renderMarkdown(md, opts = {}) {
  const lines = String(md ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [];
  let listStack = []; // [{type, indent}]

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inline(para.join(" "), opts)}</p>`);
      para = [];
    }
  };
  const closeLists = (toIndent = -1) => {
    while (listStack.length && listStack[listStack.length - 1].indent > toIndent) {
      out.push(`</li></${listStack.pop().type}>`);
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = line.match(/^\s*```(\w*)/);
    if (fence) {
      flushPara();
      closeLists();
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      out.push(`<pre><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }

    if (!line.trim()) {
      flushPara();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      closeLists();
      const level = Math.min(4, Math.max(3, heading[1].length + 1));
      out.push(`<h${level}>${inline(heading[2], opts)}</h${level}>`);
      continue;
    }

    if (line.trim().startsWith("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushPara();
      closeLists();
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
      i--;
      out.push(
        `<table><thead><tr>${head.map((h) => `<th>${inline(h, opts)}</th>`).join("")}</tr></thead><tbody>` +
          rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, opts)}</td>`).join("")}</tr>`).join("") +
          "</tbody></table>",
      );
      continue;
    }

    const item = line.match(/^(\s*)([-*+•]|\d+[.)])\s+(.*)$/);
    if (item) {
      flushPara();
      const indent = item[1].replace(/\t/g, "  ").length;
      const type = /\d/.test(item[2]) ? "ol" : "ul";
      const top = listStack[listStack.length - 1];
      if (!top || indent > top.indent) {
        out.push(`<${type}><li>`);
        listStack.push({ type, indent });
      } else {
        closeLists(indent);
        const cur = listStack[listStack.length - 1];
        if (cur && cur.type !== type && cur.indent === indent) {
          out.push(`</li></${listStack.pop().type}><${type}><li>`);
          listStack.push({ type, indent });
        } else if (cur) {
          out.push("</li><li>");
        } else {
          out.push(`<${type}><li>`);
          listStack.push({ type, indent });
        }
      }
      out.push(inline(item[3], opts));
      continue;
    }

    if (listStack.length && /^\s+\S/.test(line)) {
      out.push(" " + inline(line.trim(), opts)); // continuation of a list item
      continue;
    }
    closeLists();
    para.push(line.trim());
  }
  flushPara();
  closeLists();
  return out.join("");
}
